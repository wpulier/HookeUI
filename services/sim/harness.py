"""Scenario tests for the whole stack on a simulated camera.

Starts the real camera bridge with `--simulate` (only the phone is simulated; every other
line of the bridge is the production code) and the real orchestrator pointed at it exactly
as in live use, then connects to the orchestrator's WebSocket the way the studio does.
Faults are injected through the bridge's /sim/* endpoints and the orchestrator is judged
only by what it serves, as a browser would see it.

  python -m sim.harness                      # all scenarios on a private stack
  python -m sim.harness --only freeze,crash  # a subset
  python -m sim.harness --attach             # against a stack already started by `make sim`
"""
from __future__ import annotations

import argparse
import copy
from functools import wraps
import asyncio
import hashlib
import json
import os
import signal
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import httpx
import numpy as np
import websockets

from orchestrator import contract, licences
from orchestrator.assets import store
from orchestrator.paths import OVERRIDES, Paths

REPO = Path(__file__).resolve().parents[2]
SERVICES = REPO / "services"
BRIDGE_SCRIPT = REPO / "experiments/iphone-camera-feed/scripts/iphone_bridge_server.py"
# The real leaf recording from the assets store (assets/manifest.json); --video plays another.
DEFAULT_VIDEO_ID = "2026-06-14-leaf-epidermis-3m43s"


def default_video() -> Path:
    """Resolve real simulation input only when requested, never while importing helpers."""
    return store().real_file(DEFAULT_VIDEO_ID)


RESULTS = SERVICES / "sim" / "results"
MEDIA_COPIES = 60  # recordings in each run's footage folder (scenario media-load)
LIVE_DEVICE = "iPhoneWild Camera"
# The studio's rule (contract/overlay.json, as apps/studio/src/overlay.ts): outlines fade out
# while the slide moves fast.
FADE_START_PX_PER_FRAME, FADE_END_PX_PER_FRAME = contract.FADE_START_PX, contract.FADE_END_PX
SPEED_WINDOW = contract.SPEED_WINDOW


def overlay_opacity(recent_speeds: list[float]) -> float:
    speed = max(recent_speeds or [0.0])
    t = (speed - FADE_START_PX_PER_FRAME) / (FADE_END_PX_PER_FRAME - FADE_START_PX_PER_FRAME)
    return 1.0 - min(1.0, max(0.0, t))


# --- stack -----------------------------------------------------------------------------

class Stack:
    """The bridge (simulated phone) and orchestrator as separate processes."""

    def __init__(self, video: Path, bridge_port: int, orch_port: int, start_frame: int, attach: bool) -> None:
        self.video = video
        self.bridge_url = f"http://127.0.0.1:{bridge_port}"
        self.orch_url = f"http://127.0.0.1:{orch_port}"
        self.ws_url = f"ws://127.0.0.1:{orch_port}/ws"
        self.start_frame = start_frame
        self.attach = attach
        self.procs: list[subprocess.Popen] = []
        self.teardown: list[dict] = []
        self.http = httpx.AsyncClient(timeout=15.0)
        self.log_dir = SERVICES / "logs"

    async def __aenter__(self) -> "Stack":
        if not self.attach:
            self.log_dir.mkdir(exist_ok=True)
            for url in (self.bridge_url, self.orch_url):
                if await self._reachable(url):
                    raise SystemExit(f"{url} is already in use; stop it or pass other ports")
            bridge_port = self.bridge_url.rsplit(":", 1)[1]
            orch_port = self.orch_url.rsplit(":", 1)[1]
            # The bridge's footage folder for this run: the video under MEDIA_COPIES names, so the
            # library, thumbnails and uploads are exercised fresh (scenario media-load) and the real
            # assets store is never touched.
            import tempfile

            self.footage = Path(tempfile.mkdtemp(prefix="micromind-sim-footage-"))
            (self.footage / "corpus").mkdir()
            for n in range(MEDIA_COPIES):
                (self.footage / "corpus" / f"test-{n:03d}.mp4").symlink_to(self.video)
            # A test run saves everything in its own folder (orchestrator/paths.py): fresh
            # descriptions every run, and nothing added to the real sessions, logs, ledger, spend,
            # datasets or recordings (the bridge's recordings go there too).
            self.home = tempfile.mkdtemp(prefix="micromind-sim-")
            paths = self.paths = Paths.under(self.home)
            self._bridge_args = dict(
                args=["python3", str(BRIDGE_SCRIPT), "--port", bridge_port, "--simulate", str(self.video),
                      "--simulate-start-frame", str(self.start_frame), "--footage-dir", str(self.footage),
                      "--recording-dir", str(paths.recordings), "--datasets-dir", str(paths.datasets),
                      # The run's own snapshots and clearer pictures, playable as a session's sources (source=asset).
                      "--media-root", str(paths.snapshots), "--media-root", str(paths.results)],
                stderr=subprocess.STDOUT,
            )
            self.procs.append(subprocess.Popen(**self._bridge_args, stdout=open(self.log_dir / "sim-bridge.log", "w")))
            global INTERACTIONS
            INTERACTIONS = paths.interaction_log
            # A path override left in the shell must not send the run's files elsewhere; keys still load.
            env = {k: v for k, v in os.environ.items() if k not in OVERRIDES.values() or k == "MICROMIND_KEYS_FILE"}
            self._orch_args = dict(
                args=[sys.executable, "-m", "orchestrator.server", "--port", orch_port, "--bridge-url", self.bridge_url],
                cwd=SERVICES, stderr=subprocess.STDOUT,
                # The catalog is never checked online from a run (catalog_news.py): it reads this Mac's catalog only.
                env={**env, "MICROMIND_HOME": str(paths.home), "MICROMIND_RECORDINGS_DIR": str(paths.recordings),
                     "MICROMIND_CATALOG_AUTO": "0"},
            )
            self.procs.append(subprocess.Popen(**self._orch_args, stdout=open(self.log_dir / "sim-orchestrator.log", "w")))
        await self.wait(lambda h: h.get("state") == "live" and h["gpu"]["ready"], timeout=120, what="stack ready")
        return self

    async def restart_orchestrator(self) -> None:
        """Stop the orchestrator and start it again with the same folders (a restart mid-session)."""
        proc = self.procs[1]
        receipt = await asyncio.to_thread(self._stop_child, proc, "orchestrator", "restart", signal.SIGINT, 20.0)
        if not receipt["reaped"]:
            raise RuntimeError("the previous orchestrator did not exit; see teardown receipt")
        self.procs[1] = subprocess.Popen(**self._orch_args, stdout=open(self.log_dir / "sim-orchestrator.log", "a"))
        await self.wait(lambda h: h.get("state") == "live" and h["gpu"]["ready"], timeout=120, what="stack ready again")

    async def restart_bridge(self) -> None:
        """Stop the camera bridge and start it again (a new run of it: its frame numbers start again),
        as `make start` does (SIGTERM, which the bridge always handles; SIGINT is ignored by a process
        started in the background of a shell); the orchestrator reconnects by itself."""
        proc = self.procs[0]
        receipt = await asyncio.to_thread(self._stop_child, proc, "bridge", "restart", signal.SIGTERM, 20.0)
        if not receipt["reaped"]:
            raise RuntimeError("the previous bridge did not exit; see teardown receipt")
        self.procs[0] = subprocess.Popen(**self._bridge_args, stdout=open(self.log_dir / "sim-bridge.log", "a"))
        await self.wait(lambda h: h.get("state") == "live", timeout=60, what="live again after the bridge restarted")

    async def __aexit__(self, *exc) -> None:
        await self.http.aclose()
        # Keep each original 8s graceful wait; a forced kill has its own bounded reap.
        await asyncio.gather(*(asyncio.to_thread(self._stop_child, proc, "bridge" if i == 0 else "orchestrator",
                                                "shutdown", signal.SIGTERM if i == 0 else signal.SIGINT, 8.0)
                               for i, proc in enumerate(self.procs)))

    def _stop_child(self, proc, role: str, phase: str, stop_signal: int, budget: float) -> dict:
        began = time.monotonic()
        receipt = {"pid": proc.pid, "role": role, "phase": phase, "signal": int(stop_signal),
                   "gracefulTimeoutS": budget, "killReapTimeoutS": 0.5,
                   "forcedKill": False, "reaped": False, "returncode": None, "ok": False}
        try:
            try:
                proc.send_signal(stop_signal)
            except ProcessLookupError:
                pass  # still wait for and retain the already-exited child's status
            try:
                proc.wait(timeout=budget)
            except subprocess.TimeoutExpired:
                receipt["forcedKill"] = True
                proc.kill()
                proc.wait(timeout=receipt["killReapTimeoutS"])
            receipt["returncode"] = proc.returncode
            receipt["reaped"] = proc.returncode is not None
            receipt["ok"] = receipt["reaped"] and not receipt["forcedKill"] and proc.returncode in (0, -stop_signal)
        except Exception as error:
            receipt["error"] = f"{type(error).__name__}: {error}"
            receipt["returncode"] = proc.returncode
        receipt["stopElapsedS"] = round(time.monotonic() - began, 3)
        # Snapshot every byte present after waiting, before this harness starts another incarnation.
        # Parent exit cannot establish that descendant writers finished their stderr;
        # newline and forced-kill facts are warning signals, not proof of stream completion.
        source = self.log_dir / f"sim-{role}.log"
        retained = self.log_dir / f"sim-{role}-{proc.pid}-{phase}-{time.monotonic_ns()}.log"
        try:
            digest, count, tail = hashlib.sha256(), 0, b""
            with source.open("rb") as reader, retained.open("xb") as writer:
                remaining = source.stat().st_size  # bounded even if a surviving descendant still writes
                while remaining:
                    block = reader.read(min(65536, remaining))
                    if not block:
                        break
                    writer.write(block)
                    digest.update(block)
                    count += len(block)
                    remaining -= len(block)
                    tail = block[-1:]
            receipt["log"] = {"path": str(retained), "source": str(source), "bytes": count,
                              "sha256": digest.hexdigest(), "snapshotComplete": not remaining,
                              "writerClosureObserved": False, "endsWithNewline": tail in (b"", b"\n"),
                              "mayBeIncomplete": bool(
                                  receipt["forcedKill"] or not receipt["reaped"] or remaining or tail not in (b"", b"\n"))}
        except OSError as error:
            receipt["log"] = {"source": str(source), "snapshotComplete": False,
                              "writerClosureObserved": False, "mayBeIncomplete": True,
                              "error": f"{type(error).__name__}: {error}"}
            receipt["ok"] = False
        receipt["elapsedS"] = round(time.monotonic() - began, 3)
        self.teardown.append(receipt)
        return receipt

    async def _reachable(self, url: str) -> bool:
        try:
            await self.http.get(f"{url}/api/health", timeout=1.0)
            return True
        except httpx.HTTPError:
            return False

    async def orch_health(self) -> dict:
        return (await self.http.get(f"{self.orch_url}/api/health")).json()

    async def bridge_health(self) -> dict:
        return (await self.http.get(f"{self.bridge_url}/api/health")).json()

    async def sim(self, action: str, **params) -> dict:
        return (await self.http.post(f"{self.bridge_url}/sim/{action}", params=params)).json()

    async def sim_status(self) -> dict:
        return (await self.http.get(f"{self.bridge_url}/sim/status")).json()

    async def seek(self, frame: int, timeout: float = 10.0) -> bool:
        """Jump the footage to `frame` and wait until the bridge is serving it. A seek restarts
        the bridge's capture (its monitor notices within a second, then FFmpeg starts), so the
        new view arrives 0.4-1.5 s later; until then the old view is still fresh and "live".
        A click in that window outlines the old view, and the scenario then measures it
        against a different part of the recording (ground truth: /sim/status)."""
        before = (await self.sim_status()).get("latestBridgeFrame") or 0
        await self.sim("seek", frame=frame)
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            status = await self.sim_status()
            source, total = status.get("latestSourceFrame"), status.get("totalFrames") or 1
            if (status.get("latestBridgeFrame") or 0) > before and source is not None and (source - frame) % total < 90:
                return True
            await asyncio.sleep(0.1)
        return False

    async def reconnect(self) -> dict:
        """The camera console's Reconnect button: the bridge's hard reconnect."""
        return (await self.http.post(f"{self.bridge_url}/api/restart", timeout=40.0)).json()

    async def wait(self, predicate, timeout: float, what: str, source: str = "orch") -> tuple[bool, float]:
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            try:
                health = await (self.orch_health() if source == "orch" else self.bridge_health())
                if predicate(health):
                    return True, time.monotonic() - start
            except (httpx.HTTPError, json.JSONDecodeError):
                pass
            await asyncio.sleep(0.25)
        if what == "stack ready":
            raise SystemExit(f"Stack did not become ready within {timeout}s; see {self.log_dir}")
        return False, time.monotonic() - start


# --- the browser's view ------------------------------------------------------------------

@dataclass
class Viewer:
    """Connects like the studio and records exactly what a browser would receive."""

    url: str
    frames: list[tuple[float, dict, bytes]] = field(default_factory=list)
    scene: dict[str, dict] = field(default_factory=dict)
    scene_version: int | None = None  # the delivered scene's public revision, not the server's latest one
    candidates: list[dict] = field(default_factory=list)
    candidates_frame: int = -1
    results: list[dict] = field(default_factory=list)
    result_arrived: asyncio.Event = field(default_factory=asyncio.Event)
    _connection_settled: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _connection_error: str = field(default="", repr=False)
    failure: str = ""  # unexpected receiver failure, retained even after a successful reconnect
    keep_jpegs: bool = False
    recording: list | None = None  # flight recorder: every frame while set
    magnifier: dict = field(default_factory=dict)
    chats: list[dict] = field(default_factory=list)
    action_updates: list[dict] = field(default_factory=list)  # tools of earlier answers that finished (chat-action)
    notices: list[tuple[int, dict]] = field(default_factory=list)  # actual public activity, separate from model chat
    notice_count: int = 0  # received cursor stays monotonic when the bounded history is trimmed
    research: dict[str, dict] = field(default_factory=dict)
    notes: dict[str, dict] = field(default_factory=dict)
    presenter: dict = field(default_factory=dict)
    slidemap: dict = field(default_factory=dict)  # the slide map panel's state (orchestrator/slidemap.py)
    day: dict = field(default_factory=dict)  # the session and its conversations
    session: dict = field(default_factory=dict)  # the session open now, as the header chip shows it (ADR 0017)
    sessions: dict = field(default_factory=dict)  # every session, as the chip's menu and the sessions rail list them
    cleared: int = 0  # session-cleared messages received (another session opened)
    last_cleared: dict = field(default_factory=dict)  # the latest one: which session opened, the one left and why
    thread: dict = field(default_factory=dict)  # the active conversation as replayed
    dataset: dict = field(default_factory=dict)  # Record's dataset and its Data light (orchestrator/datasets/)
    population: dict = field(default_factory=dict)  # the population shown over the live view (population.py)
    layers: list = field(default_factory=list)  # (time, layer state): which members are verified in place
    trails: dict = field(default_factory=dict)  # the latest trails message: followed outlines' own places (trail.py)
    sources: dict = field(default_factory=dict)  # the open session's sources (ADR 0022; sources.py), as last sent
    settings: dict = field(default_factory=dict)  # selected public model settings
    scale: dict = field(default_factory=dict)  # the scale wire (scale.py): its objective, and the asset on stage's own
    placements: list = field(default_factory=list)  # `action-placed`: where a This Tab run on the Stage's still has its result

    async def run(self) -> None:
        try:
            await self._run()
        except websockets.exceptions.ConnectionClosed:
            pass  # a scenario may restart the orchestrator
        except Exception as exc:
            self.failure = self._connection_error = f"{type(exc).__name__}: {exc}"
        finally:
            # A refused connection or a later disconnect must wake senders too. The main
            # harness can reconnect after a scenario restarts the orchestrator.
            if not self._connection_error:
                self._connection_error = "observer connection closed"
            self._connection_settled.set()

    async def ready(self) -> None:
        try:
            await asyncio.wait_for(self._connection_settled.wait(), timeout=15)
        except TimeoutError as exc:
            raise TimeoutError("observer connection did not become ready within 15s") from exc
        if self._connection_error:
            raise ConnectionError(self._connection_error)

    async def _run(self) -> None:
        async with websockets.connect(self.url, max_size=None) as ws:
            self.ws = ws
            await ws.send(json.dumps({"type": "debug", "candidates": True}))
            self._connection_settled.set()
            async for msg in ws:
                now = time.time()
                if isinstance(msg, bytes):
                    n = struct.unpack(">I", msg[4:8])[0]
                    header = json.loads(msg[8 : 8 + n])
                    jpeg = msg[8 + n :]
                    self.frames.append((now, header, jpeg))
                    if self.recording is not None:
                        self.recording.append((now, header, jpeg))
                    if not self.keep_jpegs and len(self.frames) > 2:
                        t, h, _ = self.frames[-3]
                        self.frames[-3] = (t, h, b"")
                    if len(self.frames) > 20000:
                        del self.frames[:5000]
                else:
                    data = json.loads(msg)
                    if data["type"] == "scene":
                        self.scene = {o["id"]: o for o in data["objects"]}
                        self.scene_version = data.get("version")
                    elif data["type"] == "candidates":
                        self.candidates, self.candidates_frame = data["items"], data["frameId"]
                    elif data["type"] == "result":
                        self.results.append(data)
                        self.result_arrived.set()
                    elif data["type"] == "magnifier":
                        self.magnifier = data
                    elif data["type"] == "chat":
                        self.chats.append({**data, "at": now})
                    elif data["type"] == "chat-action":
                        self.action_updates.append({**data, "at": now})
                    elif data["type"] == "app-notice":
                        self.notice_count += 1
                        self.notices.append((self.notice_count, data))
                        del self.notices[:-256]
                    elif data["type"] == "research":
                        self.research[data["session"]["rid"]] = {**data["session"], "at": now}
                    elif data["type"] == "presenter":
                        self.presenter = data
                    elif data["type"] == "slidemap":
                        self.slidemap = data
                    elif data["type"] == "day":
                        self.day = data
                    elif data["type"] == "session":
                        self.session = data
                    elif data["type"] == "sessions":
                        self.sessions = data
                    elif data["type"] == "session-cleared":
                        self.cleared += 1
                        self.last_cleared = data
                    elif data["type"] == "thread":
                        self.thread = data
                    elif data["type"] == "dataset":
                        self.dataset = data
                    elif data["type"] == "population":
                        self.population = data
                    elif data["type"] == "layer":
                        self.layers.append((now, data))
                    elif data["type"] == "trails":
                        self.trails = data
                    elif data["type"] == "sources":
                        self.sources = data
                    elif data["type"] == "settings":
                        self.settings = data["settings"]
                    elif data["type"] == "scale":
                        self.scale = data
                    elif data["type"] == "action-placed":
                        self.placements.append(data)
                    elif data["type"] == "notes":
                        if "full" in data:
                            self.notes = dict(data["full"])
                        self.notes.update(data.get("upsert") or {})
                    elif data["type"] == "research-list":
                        self.research.update({s["rid"]: s for s in data["sessions"]})

    async def send(self, command: dict) -> None:
        await self.ready()
        await self.ws.send(json.dumps(command))

    def research_activity(self, rid: str, run: dict, *, since: int) -> list[dict]:
        """Received completion for this exact original attempt, never chat or a later run's facts."""
        if self.notices and since < self.notices[0][0] - 1:
            return []  # trimmed operation interval: its original completion cardinality is unknown
        if not run.get("runId"):
            return []  # unknown original identity cannot certify an announcement
        expected = {key: run.get(key) for key in ("runId", "run", "origin")}
        return [notice for cursor, notice in self.notices if cursor > since
                and notice.get("producer") == {"kind": "research", "id": rid}
                and notice.get("event") == "completed"
                and (facts := notice.get("facts") or {}).get("researchId") == rid
                and facts.get("status") == "done"
                and facts.get("run") == expected]

    def latest(self) -> tuple[float, dict, bytes]:
        return self.frames[-1]

    def frames_since(self, t: float) -> list[tuple[float, dict, bytes]]:
        return [f for f in self.frames if f[0] >= t]

    async def result_for(self, kind: str, since: int, timeout: float = 10.0, *, request_id: str | None = None) -> dict | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.result_arrived.clear()
            for r in self.results[since:]:
                if r.get("for") == kind and (request_id is None or r.get("requestId") == request_id):
                    return r
            # A polling sleep used to add up to 50 ms to a click's measured time, and could
            # miss an already-received response at the deadline. Wake when the wire result arrives.
            try:
                await asyncio.wait_for(self.result_arrived.wait(), max(0, end - time.monotonic()))
            except asyncio.TimeoutError:
                return None
        return None


# --- scenarios ---------------------------------------------------------------------------

@dataclass
class Check:
    what: str
    ok: bool
    detail: str = ""


@dataclass
class Outcome:
    name: str
    checks: list[Check] = field(default_factory=list)
    flight: dict | None = None  # frames recorded during the scenario, saved on failure
    observations: list[dict] = field(default_factory=list)  # public state/reply witnesses, also retained on failure

    def check(self, what: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(what, bool(ok), detail))
        return bool(ok)

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)


async def scenario_live_feed(stack: Stack, viewer: Viewer) -> Outcome:
    out = Outcome("live feed looks like the phone")
    bridge = await stack.bridge_health()
    orch = await stack.orch_health()
    out.check("bridge reports the phone's device name", bridge["targetDevice"] == LIVE_DEVICE, bridge["targetDevice"])
    out.check("orchestrator sees the same device name", orch["bridge"]["targetDevice"] == LIVE_DEVICE, orch["bridge"]["targetDevice"])
    t0 = time.time()
    await asyncio.sleep(4)
    got = viewer.frames_since(t0)
    fps = (len(got) - 1) / (got[-1][0] - got[0][0]) if len(got) > 1 else 0
    delays = [(t - h["t"]) * 1000 for t, h, _ in got]
    out.check("browser receives ~30 fps", 27 <= fps <= 33, f"{fps:.1f} fps")
    out.check("orchestrator-to-browser delay p95 < 50 ms", np.percentile(delays, 95) < 50, f"p95 {np.percentile(delays, 95):.0f} ms")
    out.check("frames are 1280x720", got[-1][1]["w"] == 1280 and got[-1][1]["h"] == 720, f"{got[-1][1]['w']}x{got[-1][1]['h']}")
    out.check("eyepiece detected", got[-1][1]["fov"] is not None, str(got[-1][1]["fov"]))
    ids = [h["id"] for _, h, _ in got]
    out.check("frame ids strictly increase", all(b > a for a, b in zip(ids, ids[1:])))
    status = await stack.sim_status()
    out.check("ground truth available off the data path", status.get("latestSourceFrame") is not None, f"source frame {status.get('latestSourceFrame')}")
    return out


async def scenario_media_load(stack: Stack, viewer: Viewer) -> Outcome:
    """Test assets never slow the live view (footage_media.py): while the studio's Assets view pulls the
    library, every thumbnail is made at once and a recording is uploaded, the browser still gets the
    live frame rate and delay, and clicks stay as fast as they were just before."""
    out = Outcome("media work never slows the live view")

    async def measure(phase: str, seconds: float, clicks: int) -> tuple[float, float, list[float]]:
        t0 = time.time()
        times = []
        click_samples = []
        _, header, _ = viewer.latest()
        fov = header["fov"]
        spots = [c for c in viewer.candidates if fov is None or np.hypot(c["x"] - fov[0], c["y"] - fov[1]) < 0.6 * fov[2]] or viewer.candidates
        for c in (spots * clicks)[:clicks]:
            _, header, _ = viewer.latest()
            since = len(viewer.results)
            start = time.perf_counter()
            await viewer.send({"type": "click", "frameId": header["id"], "x": c["x"], "y": c["y"]})
            reply = await viewer.result_for("click", since)
            if reply and reply.get("ok"):
                times.append((time.perf_counter() - start) * 1000)
                click_samples.append({"at": time.time(), "roundTripMs": times[-1]})
            await asyncio.sleep(max(0.1, seconds / clicks - 0.1))
        await asyncio.sleep(max(0.0, seconds - (time.time() - t0)))
        got = viewer.frames_since(t0)
        fps = (len(got) - 1) / (got[-1][0] - got[0][0]) if len(got) > 1 else 0.0
        delay = float(np.percentile([(t - h["t"]) * 1000 for t, h, _ in got], 95)) if got else 1e9
        out.observations.append({"phase": phase, "startedAt": t0, "endedAt": time.time(),
            "clicks": click_samples,
            "frames": [{"receivedAt": t, "header": {key: h[key] for key in
                ("id", "t", "an", "bridge") if key in h}} for t, h, _ in got]})
        return fps, delay, sorted(times)

    def p95(times: list[float]) -> float:
        return times[min(len(times) - 1, int(0.95 * len(times)))] if times else 1e9

    ok, _ = await stack.wait(lambda h: h["state"] == "live", 15, "live")
    await asyncio.sleep(1.0)
    base_fps, base_delay, base_clicks = await measure("baseline", 6.0, 8)

    feed = (await stack.http.get(f"{stack.bridge_url}/feed")).content
    library = (await stack.http.get(f"{stack.bridge_url}/assets/library")).json()
    names = [i["name"] for i in library["items"]]
    out.check("the state poll stays small (the library is its own request)", len(feed) < 4000, f"{len(feed)} bytes")
    out.check("the library lists the run's footage", len(names) >= MEDIA_COPIES, f"{len(names)} recordings")

    thumbs: list[tuple[int, float]] = []

    async def thumb(name: str, gate: asyncio.Semaphore) -> None:
        async with gate:
            start = time.perf_counter()
            r = await stack.http.get(f"{stack.bridge_url}/assets/thumb", params={"name": name}, timeout=60)
            thumbs.append((r.status_code if r.headers.get("content-type") == "image/jpeg" else 0, time.perf_counter() - start))

    async def grid() -> None:  # the Assets view scrolled through every card at once, eight at a time
        gate = asyncio.Semaphore(8)
        await asyncio.gather(*(thumb(n, gate) for n in names))

    async def polls() -> None:  # the studio's poll and library checks, faster than the real 3 s
        for _ in range(20):
            await stack.http.get(f"{stack.bridge_url}/feed")
            await stack.http.get(f"{stack.bridge_url}/assets/library", headers={"If-None-Match": f'"{library["version"]}"'})
            await asyncio.sleep(0.25)

    upload: dict = {}

    async def add() -> None:  # a recording added from the studio while all that runs
        data = stack.video.read_bytes()
        r = await stack.http.post(f"{stack.bridge_url}/assets/upload", content=data, timeout=120,
                                  params={"filename": "drop.mp4", "specimen": "leaf epidermis", "title": "Added by the sim"},
                                  headers={"X-MicroscopeMind": "add-footage", "Origin": stack.orch_url,
                                           "Content-Type": "application/octet-stream"})
        upload.update(r.json(), status=r.status_code, megabytes=len(data) / 1e6)

    load_t0 = time.time()
    work = asyncio.gather(grid(), polls(), add())
    load_fps, load_delay, load_clicks = await measure("media", 8.0, 10)
    await work
    load_s = time.time() - load_t0
    out.observations.append({"kind": "click-worker-timing", "attempts": [
        {"at": r.get("at"), "attempts": [{key: a[key] for key in ("ms", "model", "ok") if key in a}
                                       for a in r.get("attempts", [])[:1]]}
        for r in _interactions("execution") if r.get("origin") == "click"][-18:]})
    made = sum(1 for status, _ in thumbs if status == 200)
    out.check("frame rate unchanged while media work runs", load_fps >= 27 and load_fps >= base_fps - 1.5,
              f"{load_fps:.1f} fps (before {base_fps:.1f})")
    out.check("orchestrator-to-browser delay p95 < 50 ms while media work runs", load_delay < 50,
              f"p95 {load_delay:.0f} ms (before {base_delay:.0f} ms)")
    out.check("clicks as fast as before (p95 within 100 ms, under 300 ms)",
              p95(load_clicks) < 300 and p95(load_clicks) <= p95(base_clicks) + 100,
              f"p95 {p95(load_clicks):.0f} ms (before {p95(base_clicks):.0f} ms)")
    out.check("every thumbnail made", made == len(names), f"{made} of {len(names)} in {load_s:.0f} s")
    out.check("the upload is listed under Added here", upload.get("ok") and (upload.get("item") or {}).get("group") == "Added here",
              f"{upload.get('status')} {upload.get('error') or (upload.get('item') or {}).get('name')} ({upload.get('megabytes', 0):.0f} MB)")
    return out



async def scenario_maintenance_during_live(stack: Stack, viewer: Viewer) -> Outcome:
    """Capability maintenance never costs the live view (ADR 0025; ticket 59's MT1). Beside the live stack, an entry
    under "Automatically maintain compatible updates" gets a compatible fix: the maintenance worker, in its own
    lowest-priority process as the app starts it, fetches it, builds its own Python, proves it and promotes it, while
    the browser keeps the live frame rate and delay and clicks stay as fast as just before. Work pinned to the old
    version still resolves it; a broken fix fails with the update kept in use; Undo goes back; nothing is left running.
    The entry lives in a scratch catalog of its own (sim/maintenance_fixture.py): this Mac's catalog is never touched,
    so this measures the maintenance process's cost to the live view, not the app's own maintenance thread."""
    import shutil

    from .maintenance_fixture import Fixture
    from .frame_observations import retain

    out = Outcome("maintenance-during-live")

    async def measure(seconds: float, clicks: int, phase: str) -> tuple[float, float, list[float]]:
        t0 = time.time()
        times = []
        _, header, _ = viewer.latest()
        fov = header["fov"]
        spots = [c for c in viewer.candidates if fov is None or np.hypot(c["x"] - fov[0], c["y"] - fov[1]) < 0.6 * fov[2]] or viewer.candidates
        for c in (spots * clicks)[:clicks]:
            _, header, _ = viewer.latest()
            since = len(viewer.results)
            start = time.perf_counter()
            await viewer.send({"type": "click", "frameId": header["id"], "x": c["x"], "y": c["y"]})
            reply = await viewer.result_for("click", since)
            if reply and reply.get("ok"):
                times.append((time.perf_counter() - start) * 1000)
            await asyncio.sleep(max(0.1, seconds / clicks - 0.1))
        await asyncio.sleep(max(0.0, seconds - (time.time() - t0)))
        got = viewer.frames_since(t0)
        observed_until = time.time()
        fps = (len(got) - 1) / (got[-1][0] - got[0][0]) if len(got) > 1 else 0.0
        delay = float(np.percentile([(t - h["t"]) * 1000 for t, h, _ in got], 95)) if got else 1e9
        retain(out, phase, got, t0, observed_until)
        return fps, delay, sorted(times)

    def p95(times: list[float]) -> float:
        return times[min(len(times) - 1, int(0.95 * len(times)))] if times else 1e9

    async def while_running(process, phase: str) -> tuple[dict, list[float], list[float], list[float]]:
        fps, delays, clicks = [], [], []
        while process.poll() is None:
            f, d, c = await measure(4.0, 5, phase)
            fps.append(f)
            delays.append(d)
            clicks += c
        stdout, _ = await asyncio.to_thread(process.communicate)
        try:
            row = json.loads(stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            row = {"state": "no answer", "says": stdout[-300:]}
        return row, fps, delays, sorted(clicks)

    f = Fixture()
    try:
        await asyncio.to_thread(f.turn_on, "auto")  # v1 installed by Install and proven, before anything is measured
        first = await asyncio.to_thread(f.status)
        v1 = first["active"]["generation"]
        await asyncio.to_thread(f.pin, v1, "recipe", "sim-recipe")  # a saved recipe made on v1
        await stack.wait(lambda h: h["state"] == "live", 15, "live")
        base_fps, base_delay, base_clicks = await measure(6.0, 8, "baseline")

        await asyncio.to_thread(f.publish, "2", adapter="# a compatible fix: nothing it declares changes")
        row, fps, delays, clicks = await while_running(f.attempt(), "compatible-update")
        now = await asyncio.to_thread(f.status)
        out.check("a compatible fix is applied by itself", row.get("state") == "applied" and now["active"]["version"] == "2",
                  f"{row.get('says')}; active {now['active']['version']}")
        out.check("work pinned to the old version still runs it", await asyncio.to_thread(f.resolves, v1) == "1",
                  f"held: {now.get('held')}")
        out.check("frame rate unchanged while it prepares, proves and promotes",
                  bool(fps) and min(fps) >= 27 and min(fps) >= base_fps - 1.5,
                  f"{min(fps or [0]):.1f} fps at the least over {len(fps)} windows (before {base_fps:.1f})")
        out.check("orchestrator-to-browser delay p95 < 50 ms meanwhile", bool(delays) and max(delays) < 50,
                  f"p95 {max(delays or [1e9]):.0f} ms at the most (before {base_delay:.0f} ms)")
        out.check("clicks as fast as before meanwhile (p95 within 100 ms, under 300 ms)",
                  bool(clicks) and p95(clicks) < 300 and p95(clicks) <= p95(base_clicks) + 100,
                  f"p95 {p95(clicks):.0f} ms over {len(clicks)} clicks (before {p95(base_clicks):.0f} ms)")

        await asyncio.to_thread(f.publish, "3", adapter="def run(picture, params):\n    return 'not a table'")
        # Two publications occur within the scheduled discovery cache interval. Use the
        # ordinary explicit Check/Retry path to inspect the newly published revision.
        row, _, _, _ = await while_running(f.attempt(again=True), "failed-update")
        now = await asyncio.to_thread(f.status)
        out.check("a broken fix fails and the working version stays in use",
                  row.get("state") == "failed" and now["active"]["version"] == "2" and now["says"].endswith("2 stays in use"),
                  f"attempt {row.get('state')}: {row.get('says')}; {now['says']}")
        undo = await asyncio.to_thread(lambda: f.run("orchestrator.maintenance", "undo", "sim-maintained-spot", check=False))
        now = await asyncio.to_thread(f.status)
        out.check("Undo goes back to the version before", undo.returncode == 0 and now["active"]["version"] == "1",
                  f"active {now['active']['version']}; policy {now['policy'].get('effective')} ({now['policy'].get('held')})")
        left = await asyncio.to_thread(f.leftovers)
        out.check("nothing of maintenance is left running", not left, "; ".join(left[:3]))
    finally:
        shutil.rmtree(f.root, ignore_errors=True)
    return out


# A busy process for an outside CPU load: it spins until its parent is gone, so a harness or bench that dies
# never leaves it running (a load left behind once failed another run's timing checks).
BUSY = "import os, sys\nparent = int(sys.argv[1])\nwhile os.getppid() == parent:\n    pass"  # argv: the parent's pid


def busy_process() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", BUSY, str(os.getpid())])
MICROSAM_PYTHON = Path(os.environ.get("MICROMIND_MICROSAM_PYTHON")
                       or Path.home() / ".cache" / "micromind" / "venvs" / "microsam" / "bin" / "python").expanduser()


async def scenario_live_under_load(stack: Stack, viewer: Viewer) -> Outcome:
    """The live view never falls behind the camera (the audit of 2026-10-12: on demo night every stage queued frames,
    and under load the studio fell further and further behind). Under an outside CPU load of twice the cores, with the
    camera recording running, every stage keeps only the newest frame: what the app shows stays within a few frames
    of what the bridge has, that distance does not grow, frames never stop, and the bridge's own rate is unaffected.
    Frames the app had no time for are skipped (counted), never queued; holding the rate itself is the quality
    pacer's job."""
    from .frame_observations import retain

    out = Outcome("live-under-load")
    seconds = float(os.environ.get("SIM_UNDER_LOAD_S") or 40)
    await stack.http.post(f"{stack.bridge_url}/api/record/start", params={"owner": "sim"})
    load = [busy_process() for _ in range(2 * (os.cpu_count() or 4))]
    behind: list[int] = []
    dropped = None
    t0 = time.time()
    try:
        b0, m0 = (await stack.bridge_health()).get("frameCount", 0), time.monotonic()
        while time.time() - t0 < seconds:
            await asyncio.sleep(1.0)
            made = (await stack.bridge_health()).get("frameCount", 0)
            shown = viewer.frames_since(time.time() - 1.5)
            if shown and shown[-1][1].get("seq"):
                behind.append(made - int(shown[-1][1]["seq"]))
        last = await stack.bridge_health()
        b1, m1 = last.get("frameCount", 0), time.monotonic()
        dropped = (last.get("recording") or {}).get("dropped")
    finally:
        for process in load:
            process.kill()
        for process in load:
            process.wait()
        load_stopped_at = time.time()
        await stack.http.post(f"{stack.bridge_url}/api/record/stop", params={"owner": "sim"})
    got = viewer.frames_since(t0)
    observed_until = time.time()
    arrivals = [t for t, _, _ in got]
    fps = (len(got) - 1) / (arrivals[-1] - arrivals[0]) if len(arrivals) > 1 else 0.0
    delay = float(np.percentile([(t - h["t"]) * 1000 for t, h, _ in got], 95)) if got else 1e9
    gap = max([b - a for a, b in zip(arrivals, arrivals[1:])] or [seconds])
    quarter = max(1, len(behind) // 4)
    first, last = (float(np.median(behind[:quarter])), float(np.median(behind[-quarter:]))) if behind else (1e9, 1e9)
    skipped = (await stack.orch_health()).get("skipped")
    out.check("the view stays within 10 frames of the camera", bool(behind) and max(behind) <= 10,
              f"behind by {min(behind or [0])}-{max(behind or [0])} frames over {len(behind)} s; the app showed {fps:.1f} fps, skipped {skipped}")
    out.check("the distance never grows", last <= first + 3, f"median behind {first:.0f} frames first, {last:.0f} last")
    out.check("delay p95 under 150 ms", delay < 150, f"p95 {delay:.0f} ms")
    out.check("frames never stop for a second", gap < 1.0, f"largest gap {gap * 1000:.0f} ms")
    out.check("the bridge kept its rate while recording", (b1 - b0) / max(1e-6, m1 - m0) >= 27,
              f"{(b1 - b0) / max(1e-6, m1 - m0):.1f} fps; the recording dropped {dropped} frames")
    retain(out, "outside-cpu-load-and-recording", got, t0, observed_until)
    out.observations[-1]["windows"][-1]["cpuLoadStoppedAt"] = load_stopped_at
    return out


def preload_accounted_for(worker: str, model: str, workers: dict, preloads: list[dict],
                          unloads: list[dict], loads: list[dict]) -> bool:
    records = [r for r in preloads if r.get('worker') == worker and r.get('model') == model]
    if not records or not all(r.get('ok') is True for r in records):
        return False
    current = workers.get(worker) or {}
    if not current.get('ready') or current.get('hung') or model in (current.get('loading') or {}):
        return False
    if model in (current.get('resident') or []):
        return True
    attempts = [r for r in loads if r.get('worker') == worker and r.get('model') == model
                and r.get('state') in ('loading', 'loaded', 'failed')]
    last = max(r.get('t', 0) for r in records + attempts)
    return any(r.get('worker') == worker and r.get('model') == model and r.get('ok') is True
               and r.get('for') == 'pressure' and r.get('t', 0) > last for r in unloads)


async def scenario_model_load(stack: Stack, viewer: Viewer) -> Outcome:
    from .outline_fixture import held_outlines

    out = None
    try:
        async with held_outlines(stack, viewer) as spots:
            out = await _model_load(stack, viewer, spots)
    except Exception as exc:
        if out is None:
            raise  # the scenario itself did not run: the runner says so
        # Its phases were measured: a failure to put the held view back is one more failed check beside them,
        # never in their place (2026-10-14: the bridge gone at cleanup discarded every phase's result).
        out.check("the held view was put back", False, f"{type(exc).__name__}: {exc}")
    return out


async def _model_load(stack: Stack, viewer: Viewer, spots: list[dict]) -> Outcome:
    """Nothing freezes, whatever model loads (live 2026-10-11 02:38-02:39: 8 frames reached the studio in 80 s,
    one frame's publish took 46.8 s, the follow worker was reported hung after 42 s and the load reached 142 on
    14 cores while micro-sam loaded cold on the analysis worker; the view had been slowing since 02:36, under load
    from outside the app). The gate is what the app controls: the event loop never stalls past 200 ms, clicks stay
    under 300 ms at p95, the follower keeps stepping and is never marked hung, frames never stop for a second, and
    with no outside load the frame rate and delay are what they were before. Under an outside CPU load the view
    may slow, never stall, and the meter names the cause.

    The phases: a baseline (with two outlines followed throughout); an outside load (one busy process per core);
    a test load on the analysis worker (`gpu-test`: waiting, then as many threads as a model load may use there,
    at its priority); one real model loaded cold, micro-sam where it is installed, else SAM 2.1, for one click,
    while the clicks around it use the default outline model."""
    out = Outcome("nothing freezes while a model loads")
    ok, _ = await stack.wait(lambda h: h["state"] == "live" and h["motion"] and h["motion"]["tracked"], 15, "live")
    await asyncio.sleep(1.0)
    if not out.check("labelled structures on the held view", len(spots) >= 2, f"{len(spots)}"):
        return out
    for c in spots[:2]:  # two outlines followed throughout (the follower runs on its own worker)
        since = len(viewer.results)
        _, header, _ = viewer.latest()
        await viewer.send({"type": "click", "frameId": header["id"], "x": c["x"], "y": c["y"], "identify": False})
        await viewer.result_for("click", since)
    await asyncio.sleep(3.0)  # the follower's worker starts and loads EdgeTAM's video model

    ids = iter(range(1, 10**6))

    async def reply_to(request: str, timeout: float) -> dict | None:
        """This request's own reply (its requestId): a slow click's reply is never taken for another's."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for r in reversed(viewer.results):
                if r.get("requestId") == request:
                    return r
            viewer.result_arrived.clear()
            try:
                await asyncio.wait_for(viewer.result_arrived.wait(), max(0.0, end - time.monotonic()))
            except asyncio.TimeoutError:
                break
        return None

    async def send(command: dict) -> str:
        request = f"model-load-{next(ids)}"
        await viewer.send({**command, "requestId": request})
        return request

    async def click(c: dict) -> float | None:
        _, header, _ = viewer.latest()
        start = time.perf_counter()
        request = await send({"type": "click", "frameId": header["id"], "x": c["x"], "y": c["y"], "identify": False})
        reply = await reply_to(request, 20)
        return (time.perf_counter() - start) * 1000 if reply and reply.get("ok") else None

    async def measure(phase: str, seconds: float, clicks: int, watch=None) -> dict:
        """The view, clicks, the loop and the follower over `seconds`; `watch(health)` sees health each second."""
        h0 = await stack.orch_health()
        t0 = time.time()
        times = []
        seen: list[dict] = []

        async def poll() -> None:
            while time.time() - t0 < seconds:
                await asyncio.sleep(1.0)
                try:
                    health = await stack.orch_health()
                except Exception:
                    continue
                seen.append(health)
                if watch is not None:
                    watch(health)

        b0, m0 = (await stack.bridge_health()).get("frameCount", 0), time.monotonic()  # where frames are lost under load
        polling = asyncio.create_task(poll())
        for c in (spots * clicks)[:clicks]:
            ms = await click(c)
            if ms is not None:
                times.append(ms)
            await asyncio.sleep(max(0.1, seconds / max(clicks, 1) - 0.1))
        await asyncio.sleep(max(0.0, seconds - (time.time() - t0)))
        await polling
        h1 = await stack.orch_health()
        b1, m1 = (await stack.bridge_health()).get("frameCount", 0), time.monotonic()
        got = viewer.frames_since(t0)
        arrivals = [t for t, _, _ in got]
        lags = [r for r in _interactions("loop-lag") if r.get("since", 0) >= t0 - 0.5]
        said_hung = [r.get("problem") for r in _interactions("worker-hung")
                     if time.mktime(time.strptime(r.get("at", "1970-01-01T00:00:00"), "%Y-%m-%dT%H:%M:%S")) >= int(t0) - 1]
        # Retain the samples already used by this phase, even when its gate fails.
        # Source sequence gaps and skipped/reconnect counters distinguish intake
        # loss from analysis/receipt tails; rounded percentiles cannot do that.
        # No JPEGs, extra health requests, or changes to the measured workload.
        out.observations.append({
            "phase": phase, "startedAt": t0, "requestedSeconds": seconds,
            "monotonicBefore": m0, "monotonicAfter": m1,
            "bridgeFramesBefore": b0, "bridgeFramesAfter": b1,
            "healthBefore": h0, "healthSamples": seen, "healthAfter": h1,
            "clickMs": list(times),
            "frames": [{"receivedAt": t, "header": h} for t, h, _ in got],
        })
        times.sort()
        analysed = [(h["an"] - h["t"]) * 1000 for _, h, _ in got if h.get("an") and h.get("t")]  # arrival to analysed
        return {
            "analyse": (float(np.percentile(analysed, 50)), float(np.percentile(analysed, 90))) if analysed else (1e9, 1e9),
            "fps": (len(got) - 1) / (arrivals[-1] - arrivals[0]) if len(arrivals) > 1 else 0.0,
            # The frames the bridge made, and those the orchestrator read from it, a second over the phase.
            "bridgeFps": (b1 - b0) / max(1e-6, m1 - m0),
            "intakeFps": ((h1.get("latestFrameId") or 0) - (h0.get("latestFrameId") or 0)) / max(1e-6, m1 - m0),
            "delay": float(np.percentile([(t - h["t"]) * 1000 for t, h, _ in got], 95)) if got else 1e9,
            "gap": max([b - a for a, b in zip(arrivals, arrivals[1:])] or [seconds]),
            "clicks": times[min(len(times) - 1, int(0.95 * len(times)))] if times else 1e9,
            "answered": len(times),
            "lags": lags, "lagMs": max([r.get("ms", 0) for r in lags] or [0]),
            "steps": ((h1.get("follow") or {}).get("steps", 0) - (h0.get("follow") or {}).get("steps", 0)),
            "hung": [w.get("hung") for h in seen + [h1] for w in h.get("workers") or [] if w.get("hung")] + said_hung,
            "health": seen,
        }

    def said(m: dict) -> str:
        where = f"; stalled at {m['lags'][0].get('stacks', [''])[0].strip().splitlines()[-1].strip()[:120]}" if m["lags"] else ""
        return (f"{m['fps']:.1f} fps (bridge made {m['bridgeFps']:.1f}, orchestrator read {m['intakeFps']:.1f}), "
                f"analysis p50/p90 {m['analyse'][0]:.0f}/{m['analyse'][1]:.0f} ms, "
                f"delay p95 {m['delay']:.0f} ms, click p95 {m['clicks']:.0f} ms ({m['answered']} answered), "
                f"largest frame gap {m['gap'] * 1000:.0f} ms, {len(m['lags'])} loop stalls (worst {m['lagMs']} ms){where}, "
                f"{m['steps']} follower steps")

    def held(name: str, m: dict, quiet: bool, base: dict | None = None) -> None:
        """What the app controls, in every phase; the frame rate and delay only with no outside load."""
        out.check(f"{name}: the event loop never stalls past 200 ms", not m["lags"], said(m))
        out.check(f"{name}: click p95 under 300 ms", m["clicks"] < 300, f"p95 {m['clicks']:.0f} ms")
        out.check(f"{name}: frames never stop for a second", m["gap"] < 1.0, f"largest gap {m['gap'] * 1000:.0f} ms")
        out.check(f"{name}: the follower keeps stepping, never marked hung", m["steps"] > 0 and not m["hung"],
                  f"{m['steps']} steps; hung: {m['hung'][:1]}")
        if quiet:
            ok = m["fps"] >= 27.0 and (base is None or m["fps"] >= base["fps"] - 1.5)
            out.check(f"{name}: frame rate as before (>= 27 fps, within 1.5 of the baseline)", ok,
                      f"{m['fps']:.1f} fps" + (f" (baseline {base['fps']:.1f})" if base else ""))
            out.check(f"{name}: delay p95 under 50 ms", m["delay"] < 50, f"p95 {m['delay']:.0f} ms")

    def busy() -> list[subprocess.Popen]:
        """The outside load: one busy process per core (SIM_BUSY_PER_CORE for more), at normal priority (the 02:36
        start of it). Each ends by itself when the harness does (its parent gone), so none is left behind."""
        return [busy_process() for _ in range(int(os.environ.get("SIM_BUSY_PER_CORE") or 1) * (os.cpu_count() or 4))]

    def stop(processes: list[subprocess.Popen]) -> None:
        for p in processes:
            p.kill()
        for p in processes:
            p.wait()

    def watcher(found: list[str]):
        def watch(health: dict) -> None:
            for w in (health.get("machine") or {}).get("ours") or []:
                if w.get("loading"):
                    found.append(f"{w.get('name')} {w.get('cpu')}%")
        return watch

    # Clicks per phase: with 5-8 a p95 was the slowest click (the lead's gate, 2026-10-11); 12 fit the same phases.
    CLICKS = 12
    if os.environ.get("SIM_SETTINGS"):  # measuring an option (e.g. '{"orb_every": "3"}'), as the studio's menus set it
        changed = await reply_to(await send({"type": "settings", "settings": json.loads(os.environ["SIM_SETTINGS"])}), 10)
        out.check("the settings measured are set", bool(changed and changed.get("ok")), str(changed)[:160])
    started = time.time()
    base = await measure("baseline", 6.0, CLICKS)
    held("baseline", base, quiet=True)

    outside_load = busy()
    try:
        await asyncio.sleep(1.0)
        outside = await measure("outside load", 8.0, CLICKS)
        top = (await stack.http.get(f"{stack.orch_url}/api/machine/top")).json().get("top", [])
    finally:
        stop(outside_load)
    held("outside load", outside, quiet=False)
    # As the meter (perf.ts verdict) does: outside programs of one name together ("Python ×14"), any above 50% named.
    grouped: dict[str, float] = {}
    for p in top:
        if not p.get("ours"):
            grouped[p["name"]] = grouped.get(p["name"], 0.0) + float(p.get("cpu", 0) or 0)
    named = [name for name, cpu in sorted(grouped.items(), key=lambda kv: -kv[1]) if cpu >= 50]
    ours = sum(float(p.get("cpu", 0) or 0) for p in top if p.get("ours"))
    out.check("outside load: the meter names an outside program as the cause", bool(named) and sum(grouped.values()) > ours,
              ", ".join(named[:3]) or str(top)[:200])
    if os.environ.get("SIM_MODEL_LOAD_PHASES") == "outside":  # measuring the frame path alone (threads.py)
        print(f"  (baseline: {said(base)})\n  (outside load: {said(outside)})", flush=True)
        return out
    await asyncio.sleep(3.0)

    # A test load on the analysis worker: waiting, then as many threads as a load may use there.
    loading: list[str] = []
    test = await send({"type": "gpu-test", "worker": "analysis", "sleep": 2.0, "seconds": 8.0})
    during = await measure("test load", 9.0, CLICKS, watch=watcher(loading))
    reply = await reply_to(test, 60)
    out.check("test load: it ran on the analysis worker", bool(reply and reply.get("ok")), str(reply)[:200])
    held("test load", during, quiet=True, base=base)
    out.check("test load: the meter names our own worker as loading", bool(loading), "; ".join(dict.fromkeys(loading)) or "never")

    # One real model, cold: micro-sam where it is installed (its own Python), else SAM 2.1 tiny; for one click,
    # while the clicks around it use the default outline model on the click worker.
    if licences.edition() == "research" and MICROSAM_PYTHON.exists():
        model, worker = "micro-sam-lm", "analysis"
    else:
        model, worker = "sam2.1-tiny", "interactive"
        why = "a research-edition model" if licences.edition() != "research" else f"not installed here ({MICROSAM_PYTHON})"
        print(f"  (micro-sam is {why}: SAM 2.1 tiny instead)", flush=True)
    before = "edgetam"  # the default outline model (options.OUTLINE), which the sim's stack runs with

    async def cold_click(c: dict) -> tuple[str, dict | None]:
        """A click outlined by `model` (chosen in the settings for it, then put back)."""
        switched = await reply_to(await send({"type": "settings", "settings": {"outline": model}}), 10)
        _, header, _ = viewer.latest()
        request = await send({"type": "click", "frameId": header["id"], "x": c["x"], "y": c["y"], "identify": False})
        await asyncio.sleep(1.0)  # the click has taken the model it was made with
        await send({"type": "settings", "settings": {"outline": before}})
        return request, switched

    real_loading: list[str] = []
    cold_t0 = time.perf_counter()
    request, switched = await cold_click(spots[1])
    cold = await measure(f"{model} loading", 10.0, CLICKS, watch=watcher(real_loading))
    reply = await reply_to(request, 300)
    cold_s = time.perf_counter() - cold_t0
    # What this checks is that a cold model answers (loaded, ran, replied), never hung or timed out: the model's own
    # "nothing distinct to outline there" at that spot is an answer too (2026-10-11 gate: 12 s, a correct refusal);
    # an outline from it is checked below, "all at once". A timeout, a hang or a worker error fails.
    answered = bool(reply) and (reply.get("ok") or "nothing distinct" in str(reply.get("error") or ""))
    out.check(f"{model} cold: it loaded and answered the click", bool(switched and switched.get("ok") and answered),
              f"{cold_s:.0f} s; {str(reply)[:160]}; settings {str(switched)[:120]}")
    held(f"{model} loading", cold, quiet=True, base=base)
    out.check(f"{model} cold: the meter names it loading", bool(real_loading), "; ".join(dict.fromkeys(real_loading)) or "never")
    unloaded = await reply_to(await send({"type": "gpu-test", "worker": worker, "unload": model}), 60)
    out.check(f"{model}: unloaded again (the memory guard's op)", bool(unloaded and unloaded.get("ok")), str(unloaded)[:160])
    await asyncio.sleep(2.0)

    # All at once, as at 02:38: the outside load, the model loading cold again and a test load beside it.
    outside_load = busy()
    try:
        await asyncio.sleep(1.0)
        test = await send({"type": "gpu-test", "worker": "analysis", "sleep": 1.0, "seconds": 10.0})
        request, _ = await cold_click(spots[0])
        both = await measure("all at once", 12.0, CLICKS)
    finally:
        stop(outside_load)
    held("all at once", both, quiet=False)
    await reply_to(test, 60)
    reply = await reply_to(request, 300)
    out.check(f"all at once: {model}'s outline came", bool(reply and reply.get("ok")), str(reply)[:160])
    await reply_to(await send({"type": "gpu-test", "worker": worker, "unload": model}), 60)
    # The defaults were ready before anyone waited for them (loads.py preload), during the phases above.
    workers = {w.get("name"): w for w in (await stack.orch_health()).get("workers") or []}
    held_now = {name: w.get("resident") or [] for name, w in workers.items()}
    wanted = [("micromind-gpu-follow", "edgetam-video"), ("micromind-gpu-heavy", "dinov2-small")]
    if licences.edition() == "research":
        wanted.append(("micromind-gpu-heavy", "cellpose-sam"))
    preloads = _interactions("model-preload")
    unloads, loads = _interactions("model-unload"), _interactions("model-load")
    out.check("every default preloaded successfully and is resident or explicitly evicted for pressure",
              all(preload_accounted_for(w, m, workers, preloads, unloads, loads) for w, m in wanted)
              and all(r.get("ok") for r in preloads),
              f"held {held_now}; preloaded {[(r.get('model'), r.get('seconds'), r.get('error')) for r in preloads]}; "
              f"pressure evictions {[(r.get('model'), r.get('at')) for r in unloads if r.get('for') == 'pressure' and r.get('ok') is True]}")
    print(f"  (model-load took {time.time() - started:.0f} s; {model} cold {cold_s:.0f} s)", flush=True)
    return out


async def scenario_footage_switch(stack: Stack, viewer: Viewer) -> Outcome:
    """Picking another recording, moving the playhead, or pausing, is quick and seamless: the bridge stops a
    recording at once (a file read needs no grace; before, every switch waited 2 s for SIGKILL), so
    the live view goes without frames for well under the studio's dim time (frameDimMs) and the app
    never leaves live."""
    out = Outcome("switching recordings and the playhead are quick")
    ok, _ = await stack.wait(lambda h: h["state"] == "live", 15, "live")
    before = (await stack.http.get(f"{stack.bridge_url}/feed")).json()  # put back at the end, as found
    names = [i["name"] for i in (await stack.http.get(f"{stack.bridge_url}/assets/library")).json()["items"]][:3]
    gaps, took, states = [], [], set()

    async def act(url: str) -> dict:
        t0 = time.time()
        body = (await stack.http.post(url, timeout=30)).json()
        t1 = time.time()
        took.append(t1 - t0)
        for _ in range(15):  # 1.5 s after: what the browser got, and whether the app stayed live
            states.add((await stack.orch_health())["state"])
            await asyncio.sleep(0.1)
        got = [t for t, _, _ in viewer.frames_since(t0 - 0.3)]
        gaps.append(max((b - a for a, b in zip(got, got[1:])), default=99.0))
        return body

    for name in names:
        body = await act(f"{stack.bridge_url}/feed?source=footage&footage={name}")
        out.check(f"switched to {name}", body.get("footage") == name, str(body.get("footage")))
    body = await act(f"{stack.bridge_url}/feed/seek?seconds=12")
    position = (body.get("playback") or {}).get("seconds", -1)
    out.check("the playhead moves the recording", 11.5 <= position <= 13.5, f"at {position} s")
    # Paused: one picture held, frames still coming (a slide at rest), and the app stays live.
    t0 = time.time()
    body = (await stack.http.post(f"{stack.bridge_url}/feed/pause", params={"paused": 1})).json()
    await asyncio.sleep(2.0)
    held = viewer.frames_since(t0 + 0.5)
    states.add((await stack.orch_health())["state"])
    playback = body.get("playback") or {}
    later = ((await stack.http.get(f"{stack.bridge_url}/feed")).json().get("playback") or {})
    positions = (playback.get("seconds"), later.get("seconds"))
    out.check("paused: the position holds", playback.get("paused") is True and later.get("paused") is True
              and all(isinstance(p, (int, float)) for p in positions) and abs(positions[1] - positions[0]) <= .05,
              f"position {positions[0]} -> {positions[1]} s")
    out.check("paused: frames keep coming at the required rate", len(held) >= 40, f"{len(held)} frames in 1.5 s")
    body = await act(f"{stack.bridge_url}/feed/pause?paused=0")
    out.check("plays on after a pause", (body.get("playback") or {}).get("paused") is False, str(body.get("playback")))
    dim_s = contract.FRAME_DIM_MS / 1000
    out.check("each switch or seek answers within 1.2 s", max(took) < 1.2, ", ".join(f"{t:.2f}" for t in took) + " s")
    out.check(f"the live view goes without frames for under {dim_s:g} s (it never dims)", max(gaps) < dim_s,
              "longest gaps " + ", ".join(f"{g:.2f}" for g in gaps) + " s")
    out.check("the app stays live throughout", states == {"live"}, ", ".join(sorted(states)))
    # Put back what played before, where it was, as the recording and asset scenarios do: left playing at another
    # moment, the next scenarios clicked points found on one frame onto frames that had moved on (the
    # 2026-10-14 control: cells-clicks answered 3 and 6 of 12 after this scenario, 12 of 12 with the feed put back).
    was = (before.get("playback") or {}).get("seconds")
    try:
        back = await stack.http.post(f"{stack.bridge_url}/feed", params={"source": "footage", "footage": before.get("footage"),
                                                                         **({"at": was} if isinstance(was, (int, float)) else {})},
                                     timeout=30)
        feed = (await stack.http.get(f"{stack.bridge_url}/feed")).json()
    except Exception as exc:
        # The bridge not answering here is this check failing, beside the checks already measured, never in their place.
        out.check("and the feed goes back to what it was", False, f"{before.get('footage')}: {type(exc).__name__}: {exc}")
        return out
    live, _ = await stack.wait(lambda h: h["state"] == "live", 15, "live on the feed it found")
    out.check("and the feed goes back to what it was", back.status_code == 200 and live
              and feed.get("footage") == before.get("footage") and not (feed.get("playback") or {}).get("paused"),
              f"{before.get('footage')} -> {feed.get('footage')} ({back.status_code})")
    return out


async def scenario_freeze(stack: Stack, viewer: Viewer) -> Outcome:
    out = Outcome("camera freezes (no frames, process alive)")
    before = await stack.bridge_health()
    t0 = time.time()
    await stack.sim("freeze", seconds=10)
    ok, took = await stack.wait(lambda h: h["state"] != "live", 6, "orchestrator leaves live")
    out.check("orchestrator stops reporting live within 4.5 s", ok and took <= 4.5, f"{took:.1f} s")
    await asyncio.sleep(0.5)
    stalled_at = time.time()
    await asyncio.sleep(1.0)
    out.check("no frames reach the browser while frozen", len(viewer.frames_since(stalled_at)) == 0)
    r = await stack.http.get(f"{stack.orch_url}/api/health")
    out.check("health explains the outage", r.json()["state"] in {"stale", "bridge_not_live"}, r.json()["state"])
    ok, took = await stack.wait(lambda h: h["state"] == "live", 15, "recovered")
    after = await stack.bridge_health()
    out.check("bridge restarted the stalled capture by itself", after["restartCount"] > before["restartCount"],
              f"restarts {before['restartCount']} -> {after['restartCount']}")
    out.check("live again without any manual action", ok, f"{time.time() - t0:.1f} s after the freeze began")
    return out


async def scenario_focus_gate(stack: Stack, viewer: Viewer) -> Outcome:
    """W1-1: a defocused view is flagged (so Sight and naming wait for focus) and the flag
    clears when the picture is sharp again; /sim/degrade blurs it like turning the focus knob."""
    out = Outcome("an out-of-focus view is flagged, then cleared")
    quality = lambda h: (h.get("view") or {}).get("quality") or []  # noqa: E731
    ok, _ = await stack.wait(lambda h: h["state"] == "live" and "blurry" not in quality(h), 15, "sharp view")
    out.check("the footage starts in focus", ok)
    await stack.sim("degrade", blur=8)
    ok, took = await stack.wait(lambda h: h["state"] == "live" and "blurry" in quality(h), 15, "blur flagged")
    out.check("a strong blur is flagged as out of focus", ok, f"{took:.1f} s after the blur")
    await stack.sim("degrade", blur=2)
    ok, took = await stack.wait(lambda h: h["state"] == "live" and h.get("view") is not None, 15, "slight blur")
    await asyncio.sleep(2.0)
    r = (await stack.http.get(f"{stack.orch_url}/api/health")).json()
    out.check("a slight blur is not", "blurry" not in quality(r), str(quality(r)))
    await stack.sim("degrade")
    ok, took = await stack.wait(lambda h: h["state"] == "live" and "blurry" not in quality(h), 15, "sharp again")
    out.check("the flag clears once it is sharp again", ok, f"{took:.1f} s")
    return out


async def scenario_content_freeze(stack: Stack, viewer: Viewer) -> Outcome:
    """A picture that does not change while frames keep arriving (a slide at rest; /sim/repeat
    sends the very same picture) is the normal state of a microscope: it stays live, keeps
    reaching the browser, and only its `still` seconds are measured (2026-10-06: a "frozen"
    state blanked the live view of a still slide)."""
    out = Outcome("a still picture stays live")
    ok, _ = await stack.wait(lambda h: h["state"] == "live", 15, "live")
    out.check("live to begin with", ok)
    await stack.sim("repeat", seconds=6)
    t0 = time.time()
    states = set()
    while time.time() - t0 < 5.0:
        states.add((await stack.http.get(f"{stack.orch_url}/api/health")).json().get("state"))
        await asyncio.sleep(0.25)
    out.check("never anything but live", states == {"live"}, str(sorted(states)))
    out.check("frames keep reaching the browser", len(viewer.frames_since(t0)) > 10, f"{len(viewer.frames_since(t0))} frames")
    latest = viewer.frames_since(t0)[-1][1] if viewer.frames_since(t0) else {}  # (time, header, jpeg)
    out.check("each frame says how long the picture has been still", (latest.get("still") or 0) >= 1.5,
              f"still {latest.get('still')} s")
    return out


async def scenario_still_change(stack: Stack, viewer: Viewer) -> Outcome:
    """W3-1: with the slide still, something swimming into view is noticed (Sight is asked to
    look again), while a still view with nothing new is not."""
    out = Outcome("something new in a still view is noticed")
    change = lambda h: ((h.get("view") or {}).get("change") or {})  # noqa: E731
    await stack.sim("degrade")  # a clean picture, captured afresh
    ok, _ = await stack.wait(lambda h: h["state"] == "live" and h.get("motion") is not None, 15, "live")
    await asyncio.sleep(6.0)
    r = (await stack.http.get(f"{stack.orch_url}/api/health")).json()
    quiet = change(r).get("kind") != "content" or change(r).get("age", 99) > 5.5
    out.check("a still view with nothing new reports nothing new", quiet, str(change(r)))
    await stack.sim("degrade", swimmer=150)
    ok, took = await stack.wait(lambda h: change(h).get("kind") == "content" and change(h).get("age", 99) < 2.0, 15,
                                "content change")
    out.check("something swimming in is noticed", ok, f"{took:.1f} s after it started")
    await stack.sim("degrade")
    return out


async def scenario_crash(stack: Stack, viewer: Viewer) -> Outcome:
    out = Outcome("capture process dies")
    before = await stack.bridge_health()
    t0 = time.time()
    await stack.sim("crash")
    restarted, _ = await stack.wait(lambda h: h["restartCount"] > before["restartCount"], 6, "bridge restart", source="bridge")
    after = await stack.bridge_health()
    out.check("bridge restarted the dead capture", restarted, f"restarts {before['restartCount']} -> {after['restartCount']}")
    resumed_from = time.time()
    ok, _ = await stack.wait(lambda h: h["state"] == "live", 12, "recovered")
    await asyncio.sleep(1.0)
    out.check("frames resume automatically", ok and bool(viewer.frames_since(resumed_from)), f"{time.time() - t0:.1f} s after the crash")
    gap = 0.0
    times = [t for t, _, _ in viewer.frames_since(t0 - 1)]
    if len(times) > 1:
        gap = max(b - a for a, b in zip(times, times[1:]))
    out.check("longest gap in the browser under 8 s", gap < 8, f"{gap:.1f} s")
    return out


async def scenario_unplug_reconnect(stack: Stack, viewer: Viewer) -> Outcome:
    out = Outcome("phone disappears, hard reconnect brings it back")
    await stack.sim("unplug", recover="true")
    ok, took = await stack.wait(lambda h: h["state"] != "live", 6, "orchestrator leaves live")
    out.check("orchestrator leaves live within 4.5 s", ok and took <= 4.5, f"{took:.1f} s")
    snapshot = await stack.http.get(f"{stack.bridge_url}/snapshot.jpg")
    out.check("bridge refuses to serve a stale snapshot", snapshot.status_code == 503, str(snapshot.status_code))
    t0 = time.time()
    result = await stack.reconnect()
    out.check("hard reconnect resets Continuity and goes live", result.get("action") == "live" and bool(result.get("continuityReset")),
              f"action={result.get('action')}")
    ok, took = await stack.wait(lambda h: h["state"] == "live", 10, "orchestrator live")
    out.check("orchestrator live again after reconnect", ok, f"{time.time() - t0:.1f} s after Reconnect")
    return out


async def scenario_unplug_unrecoverable(stack: Stack, viewer: Viewer) -> Outcome:
    out = Outcome("phone stays gone: reconnect fails honestly")
    await stack.sim("unplug", recover="false")
    await stack.wait(lambda h: h["state"] != "live", 6, "orchestrator leaves live")
    result = await stack.reconnect()
    out.check("reconnect reports the device is missing", result.get("action") == "device_missing", f"action={result.get('action')}")
    orch = await stack.orch_health()
    out.check("orchestrator does not claim live", orch["state"] != "live", orch["state"])
    await stack.sim("plug")
    result = await stack.reconnect()
    out.check("after plugging back in, reconnect goes live", result.get("action") == "live", f"action={result.get('action')}")
    ok, _ = await stack.wait(lambda h: h["state"] == "live", 10, "orchestrator live")
    out.check("orchestrator live again", ok)
    return out


def _inside_view(obj: dict, header: dict) -> bool:
    if not header.get("fov"):
        return True
    pts = np.array([p for poly in obj["polygons"] for p in poly])
    cx = pts[:, 0].mean() + header["dx"] - obj["anchorOffset"][0]
    cy = pts[:, 1].mean() + header["dy"] - obj["anchorOffset"][1]
    return bool(np.hypot(cx - header["fov"][0], cy - header["fov"][1]) <= header["fov"][2] * contract.IN_VIEW_RADIUS)


MEASURE_CLEAR_MARGIN = 0.08  # how much the true match must beat the next best (scene.refind uses the same)


def _overlay_error(header: dict, jpeg: bytes, obj: dict, template: np.ndarray) -> tuple[float, float] | None:
    """Distance between where the overlay is drawn and where the outlined structure really
    is (template match against the frame the browser received). None when that cannot be told: the
    best match is not clearly better than the next one away from it (as scene.refind requires). On a
    blurred view of repeating cells the best match is a look-alike: during the phone's refocus in
    `refind` it scored 0.61-0.78 at 24-97 px from an outline that had not moved, the runner-up
    within 0.06 of it, while sharp frames led by 0.11-0.18."""
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    shift = (header["dx"] - obj["anchorOffset"][0], header["dy"] - obj["anchorOffset"][1])
    pts = np.array(obj["polygons"][0])
    drawn = ((pts[:, 0].min() + pts[:, 0].max()) / 2 + shift[0], (pts[:, 1].min() + pts[:, 1].max()) / 2 + shift[1])
    th, tw = template.shape
    x0, y0 = int(drawn[0] - tw / 2) - 80, int(drawn[1] - th / 2) - 80
    if x0 < 0 or y0 < 0 or x0 + tw + 160 > img.shape[1] or y0 + th + 160 > img.shape[0]:
        return None
    res = cv2.matchTemplate(img[y0 : y0 + th + 160, x0 : x0 + tw + 160], template, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    others = res.copy()
    r = max(4, min(tw, th) // 3)
    cv2.rectangle(others, (loc[0] - r, loc[1] - r), (loc[0] + r, loc[1] + r), -1.0, -1)
    if float(others.max()) > score - MEASURE_CLEAR_MARGIN:
        return None
    actual = (x0 + loc[0] + tw / 2, y0 + loc[1] + th / 2)
    return float(np.hypot(actual[0] - drawn[0], actual[1] - drawn[1])), float(score)


async def _outline_and_follow(stack: Stack, viewer: Viewer, out: Outcome, seconds: float, during=None) -> list[tuple]:
    ok, _ = await stack.wait(lambda h: h["state"] == "live" and h["motion"] and h["motion"]["tracked"], 15, "live")
    await asyncio.sleep(1.0)
    viewer.keep_jpegs = True
    viewer.recording = []
    frame_t, header, jpeg = viewer.latest()
    clicked_header = header
    # The latest frame preceded the recorder being enabled. Keep that exact input too.
    viewer.recording.append((frame_t, header, jpeg))
    if not viewer.candidates:
        out.check("candidate spots available to click", False)
        return []
    fov = header["fov"]
    inner = [c for c in viewer.candidates if fov is None or np.hypot(c["x"] - fov[0], c["y"] - fov[1]) < 0.6 * fov[2]]
    if not out.check("a candidate spot well inside the view", bool(inner), f"{len(inner)} of {len(viewer.candidates)}"):
        return []
    # A mid-sized spot near the middle of the view, like a person would point at.
    target = sorted(inner, key=lambda c: abs(c["r"] - 8))[0]
    since = len(viewer.results)
    await viewer.send({"type": "click", "frameId": header["id"], "x": target["x"], "y": target["y"]})
    reply = await viewer.result_for("click", since)
    if not out.check("click produces an outline", reply and reply.get("ok"), str(reply)):
        return []
    object_id = reply["objectId"]
    await asyncio.sleep(0.3)
    obj = viewer.scene[object_id]
    initial_object = copy.deepcopy(obj)
    gray = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    pts = np.array(obj["polygons"][0])
    x0, y0 = (pts.min(axis=0) - 10).astype(int)
    x1, y1 = (pts.max(axis=0) + 10).astype(int)
    template = gray[max(0, y0) : y1, max(0, x0) : x1].copy()
    samples = []
    start = time.time()
    if during is not None:
        asyncio.create_task(during())
    while time.time() - start < seconds:
        await asyncio.sleep(0.5)
        _, header, jpeg = viewer.latest()
        obj = viewer.scene.get(object_id)
        if obj is None or not jpeg:
            continue
        opacity = overlay_opacity([h.get("speed", 0.0) for _, h, _ in viewer.frames[-SPEED_WINDOW:]])
        # Mirrors the studio (overlay.ts isVisible + view.ts draw): tracked, same epoch, and the
        # outline's centre still inside the eyepiece.
        drawn_visibly = obj["track"] == "tracking" and obj["anchorEpoch"] == header["epoch"] and header.get("tracked", True) is not False and _inside_view(obj, header)
        measured = _overlay_error(header, jpeg, obj, template) if drawn_visibly else None
        samples.append((round(time.time() - start, 1), obj["track"], drawn_visibly, measured, opacity))
    viewer.keep_jpegs = False
    out.flight = {"frames": viewer.recording, "object": viewer.scene.get(object_id), "samples": samples,
                  "clicked": clicked_header, "initialObject": initial_object,
                  "click": {"frameId": clicked_header["id"], "x": target["x"], "y": target["y"]}}
    viewer.recording = None
    return samples


async def scenario_outline_tracking(stack: Stack, viewer: Viewer) -> Outcome:
    out = Outcome("outline stays on the specimen through a fast pan")
    # Just before the recording's fastest pan; the click must land on that view, not the old one.
    out.check("footage moved to the pan", await stack.seek(4330))
    samples = await _outline_and_follow(stack, viewer, out, seconds=20)
    measured = [s for s in samples if s[2] and s[3] and s[3][1] > 0.6]
    solid = [s for s in measured if s[4] >= 0.99]  # fully drawn
    showing = [s for s in measured if s[4] > 0.25]  # noticeably visible
    out.check("outline measured while shown", len(solid) >= 15, f"{len(solid)} samples fully shown")
    if solid:
        errors = [s[3][0] for s in solid]
        out.check("fully shown: median error <= 2 px", np.median(errors) <= 2, f"median {np.median(errors):.1f} px")
        bad = [f"{s[0]}s: {s[3][0]:.0f}px" for s in solid if s[3][0] > 5]
        out.check("fully shown: never more than 5 px off", not bad, f"max {max(errors):.1f} px" + (f"; off at {', '.join(bad)}" if bad else ""))
    bad = [f"{s[0]}s: {s[3][0]:.0f}px at opacity {s[4]:.2f}" for s in showing if s[3][0] > 10]
    out.check("anything visible is within 10 px (faded during fast pans)", not bad, "; ".join(bad))
    out.check("outline faded at least once during the pan", any(s[4] < 0.5 for s in samples))
    # Tissue does not swim: through the pans and the hub's corrections after them, no trail may say the
    # outline went somewhere on its own (trail.py; review of #155, T1).
    said = [f"{t['id']}: {t['words']}" for t in viewer.trails.get("trails", []) if t.get("words")]
    out.check("no trail says still tissue moved on its own", not said, "; ".join(said) or f"{len(viewer.trails.get('trails', []))} trails, none moved")
    return out


async def scenario_outline_across_outage(stack: Stack, viewer: Viewer) -> Outcome:
    out = Outcome("outline after an outage is re-found or withheld, never misplaced")

    async def outage():
        await asyncio.sleep(3)
        await stack.sim("freeze", seconds=10)

    samples = await _outline_and_follow(stack, viewer, out, seconds=25, during=outage)
    visible = [s for s in samples if s[2] and s[3] and s[3][1] > 0.6 and s[4] > 0.25]
    wrong = [s for s in visible if s[3][0] > 10]
    out.check("no visible outline drawn in the wrong place", not wrong, f"{len(wrong)} bad of {len(visible)} visible samples")
    states = sorted({s[1] for s in samples})
    out.check("outline state reported", bool(states), ", ".join(states))
    return out


async def scenario_follow_clicks(stack: Stack, viewer: Viewer) -> Outcome:
    """Option TRACK "objects" (the default): with four outlines being followed by EdgeTAM's video
    model on the heavy GPU worker, clicks stay fast (the main worker serves them), and the
    follower really ran."""
    out = Outcome("clicks stay fast while outlines are followed")
    ok, _ = await stack.wait(lambda h: h["state"] == "live" and h["motion"] and h["motion"]["tracked"], 15, "live")
    await asyncio.sleep(1.0)
    _, header, _ = viewer.latest()
    fov = header["fov"]
    inner = [c for c in viewer.candidates if fov is None or np.hypot(c["x"] - fov[0], c["y"] - fov[1]) < 0.6 * fov[2]]
    if not out.check("candidate spots inside the view", len(inner) >= 4, f"{len(inner)}"):
        return out
    for c in inner[:4]:  # four outlines to follow
        since = len(viewer.results)
        _, header, _ = viewer.latest()
        await viewer.send({"type": "click", "frameId": header["id"], "x": c["x"], "y": c["y"]})
        await viewer.result_for("click", since)
    await asyncio.sleep(3.0)  # the follower starts on the heavy worker (model load) and steps
    measured_from = time.time()
    times = []
    for c in (inner * 3)[4:14]:
        _, header, _ = viewer.latest()
        since = len(viewer.results)
        t0 = time.perf_counter()
        await viewer.send({"type": "click", "frameId": header["id"], "x": c["x"], "y": c["y"]})
        reply = await viewer.result_for("click", since)
        if reply and reply.get("ok"):
            times.append((time.perf_counter() - t0) * 1000)
        await asyncio.sleep(0.4)
    times.sort()
    p95 = times[min(len(times) - 1, int(0.95 * len(times)))] if times else None
    out.check("clicks answered", len(times) >= 8, f"{len(times)} of 10")
    out.check("click p95 under 300 ms while following", p95 is not None and p95 < 300, f"p95 {p95 and round(p95)} ms")
    steps = _interactions("object-follow", after=measured_from)
    out.check("the follower ran (object-follow logged)", bool(steps))
    # A failed step is logged too, so "it ran" alone passed while EdgeTAM failed on every step
    # once two or more objects were prompted on one frame (2026-10-06). A timeout is load, not that.
    failed = [r["error"] for r in steps if r.get("error") and not str(r["error"]).startswith("TimeoutError")]
    out.check("no follower step failed", not failed, f"{len(failed)} of {len(steps)}: {failed[0][:90]}" if failed else f"{len(steps)} steps")
    # Followed outlines' trails reach the studio (trail.py): places only where the follower verified
    # them, in one epoch per segment, and no segment joined across a break.
    trails = [t for t in viewer.trails.get("trails", []) if any(s["points"] for s in t["segments"])]
    out.check("trails sent for followed outlines", bool(trails), f"{len(trails)} trails")
    paths = [round(sum(np.hypot(b[0] - a[0], b[1] - a[1]) for s in t["segments"] for a, b in zip(s["points"], s["points"][1:])))
             for t in trails]
    # The leaf does not swim: a trail that says it went somewhere is a still thing reported as moving.
    said = [f"{t['id']}: {t['words']}" for t in trails if t.get("words")]
    out.check("no trail says the still leaf moved on its own", not said, "; ".join(said) or f"own paths {paths} px")
    return out


async def scenario_long_session(stack: Stack, viewer: Viewer) -> Outcome:
    """Contract 1 (docs/design/2026-10-06-captures-operations-evidence.md): over a long session of
    clicks, cell runs and clears, the views held for work stay inside their memory bound and level
    off: reservations never pass their allowances, and what is held between bursts of work goes back
    down instead of growing. Minutes from SIM_LONG_MINUTES (default 30). Not part of `make
    sim-test`: run with `--only long-session` (research is the default edition for the cell runs)."""
    out = Outcome("held views level off over a long session")
    minutes = float(os.environ.get("SIM_LONG_MINUTES") or 30)
    ok, _ = await stack.wait(lambda h: h["state"] == "live" and h["motion"] and h["motion"]["tracked"], 30, "live")
    if not out.check("live", ok):
        return out
    pid = stack.procs[1].pid if len(stack.procs) > 1 else None

    def rss_mb() -> float | None:
        if pid is None:
            return None
        try:
            return int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)]).strip()) / 1024
        except Exception:
            return None

    samples, refused, cells_ran, clicks = [], 0, 0, 0
    started = time.monotonic()
    next_cells, next_clear = started + 30, started + 90
    while time.monotonic() - started < minutes * 60:
        _, header, _ = viewer.latest()
        fov = header["fov"]
        spots = [c for c in viewer.candidates if fov is None or np.hypot(c["x"] - fov[0], c["y"] - fov[1]) < 0.7 * fov[2]]
        for i, c in enumerate(spots[:3]):  # a burst of clicks, the first named (naming decodes the colour view)
            since = len(viewer.results)
            await viewer.send({"type": "click", "frameId": header["id"], "x": c["x"], "y": c["y"], "identify": i == 0})
            reply = await viewer.result_for("click", since)
            clicks += 1
            refused += bool(reply and not reply.get("ok") and "held" in str(reply.get("error")))
        if time.monotonic() > next_cells:
            since = len(viewer.results)
            await viewer.send({"type": "cells"})
            reply = await viewer.result_for("cells", since, timeout=120)
            cells_ran += bool(reply and reply.get("ok"))
            next_cells = time.monotonic() + 120
        if time.monotonic() > next_clear:
            await viewer.send({"type": "clear"})
            next_clear = time.monotonic() + 180
        await asyncio.sleep(8.0)
        health = await stack.orch_health()
        held = health.get("captures") or {}
        samples.append({"t": round(time.monotonic() - started), "heldMb": held.get("heldMb"),
                        "uncounted": sum((held.get("uncountedDecodes") or {}).values()),
                        "reserved": {k: v.get("reservedMb") for k, v in (held.get("classes") or {}).items()},
                        "peak": {k: v.get("peakMb") for k, v in (held.get("classes") or {}).items()},
                        "open": held.get("openRequests"), "rssMb": rss_mb()})
    if not out.check("samples taken", len(samples) >= 10, f"{len(samples)}"):
        return out
    final = (await stack.orch_health()).get("captures", {})
    classes = final.get("classes", {})
    # What is really resident in held views (their bytes and the decodes kept on them) never exceeds
    # what was reserved for them: a decode outside a reservation would show here.
    beyond = [s for s in samples if (s["heldMb"] or 0) > sum(v or 0 for v in s["reserved"].values()) + 0.01]
    out.check("held views never keep more than was reserved for them", not beyond,
              f"{len(beyond)} samples over, e.g. {beyond[:1]}")
    uncounted = final.get("uncountedDecodes") or {}
    out.check("no decode of a held view was made outside a reservation", not uncounted, json.dumps(uncounted)[:300])
    over = [k for k, v in classes.items() if v["peakMb"] > v["allowanceMb"] + 1e-6]
    out.check("no allowance was ever passed (peak against allowance)", not over,
              ", ".join(f"{k} peak {v['peakMb']} of {v['allowanceMb']} MB" for k, v in classes.items()))
    half = len(samples) // 2
    held_first = max(s["heldMb"] or 0 for s in samples[:half])
    held_last = max(s["heldMb"] or 0 for s in samples[half:])
    out.check("held views level off (second half no higher than the first, +16 MB)", held_last <= held_first + 16,
              f"max {held_first:.1f} MB then {held_last:.1f} MB")
    opens = [s["open"] or 0 for s in samples]
    out.check("open requests do not pile up", max(opens[half:]) <= max(opens[:half]) + 5, f"max {max(opens[:half])} then {max(opens[half:])}")
    rss = [s["rssMb"] for s in samples if s["rssMb"]]
    if len(rss) > 4:
        quarter = len(rss) // 4
        growth = max(rss[-quarter:]) - max(rss[quarter:2 * quarter])
        out.check("the orchestrator's memory levels off (last quarter within 150 MB of the second)", growth <= 150,
                  f"{max(rss[quarter:2 * quarter]):.0f} -> {max(rss[-quarter:]):.0f} MB")
    print(f"  long-session: {minutes:g} min, {clicks} clicks ({refused} refused for memory), {cells_ran} cell runs, "
          f"samples {json.dumps(samples[::max(1, len(samples) // 12)])}", flush=True)
    return out


def _interactions(kind: str, *, after: float = 0) -> list[dict]:
    if INTERACTIONS is None or not Path(INTERACTIONS).exists():
        return []
    return [r for r in map(json.loads, Path(INTERACTIONS).read_text().splitlines())
            if r.get("kind") == kind and r.get("t", 0) >= after]


async def scenario_provenance(stack: Stack, viewer: Viewer) -> Outcome:
    """Contract 6: every outline a click makes runs through the runner, which records one Execution
    for it: the operation, the implementation that ran (the default, by route), who asked, and the
    view it ran on, which is the frame the click was made on."""
    out = Outcome("every click's outline says what ran, on the view it was clicked on")
    ok, _ = await stack.wait(lambda h: h["state"] == "live" and h["motion"] and h["motion"]["tracked"], 15, "live")
    _, header, _ = viewer.latest()
    fov = header["fov"]
    spots = [c for c in viewer.candidates if fov is None or np.hypot(c["x"] - fov[0], c["y"] - fov[1]) < 0.6 * fov[2]]
    if not out.check("candidate spots inside the view", len(spots) >= 5, f"{len(spots)}"):
        return out
    before = len(_interactions("execution"))
    clicked = []
    for c in spots[:5]:
        _, header, _ = viewer.latest()
        since = len(viewer.results)
        await viewer.send({"type": "click", "frameId": header["id"], "x": c["x"], "y": c["y"], "identify": False})
        reply = await viewer.result_for("click", since)
        if reply and reply.get("ok"):
            clicked.append(header["id"])
    await asyncio.sleep(1.0)
    records = [r for r in _interactions("execution")[before:] if r.get("operation") == "outline" and r.get("origin") == "click"]
    out.check("five clicks outlined", len(clicked) == 5, f"{len(clicked)}")
    out.check("one execution per click", len(records) == len(clicked), f"{len(records)} records")
    out.check("each ran on the frame it was clicked on", [r["capture"] for r in records] == clicked,
              f"{[r['capture'] for r in records]} vs {clicked}")
    out.check("the default ran, by route", all(r["executed"] and r["policy"] == "route" and r["selection"] == "settings"
                                              for r in records), f"{[(r['executed'], r['policy']) for r in records]}")
    return out


async def scenario_cells_clicks(stack: Stack, viewer: Viewer) -> Outcome:
    """Contract 5: clicks stay fast while a cell run is already going on the analysis worker (the
    click burst starts after it has begun), and following goes on beside it on its own worker.
    Research edition only (Cellpose-SAM): with another edition it says so and checks nothing."""
    out = Outcome("clicks stay fast while the cells are being found")
    if licences.edition() != "research":
        print("  (skipped: cell finders are research-edition models; run with MICROMIND_EDITION=research)", flush=True)
        return out
    ok, _ = await stack.wait(lambda h: h["state"] == "live" and h["motion"] and h["motion"]["tracked"], 15, "live")
    from .cells_overlap import resident_worker, wait_started, detail, cell_rpc, overlap
    model = viewer.settings.get("cells")
    if not out.check("selected cells model is Cellpose-SAM", model == "cellpose-sam", str(model)):
        return out
    loaded, _ = await stack.wait(lambda h: resident_worker(h, model) is not None, 120, "Cellpose resident")
    if not out.check("selected Cellpose is resident before admission", loaded):
        return out
    _, header, _ = viewer.latest()
    fov = header["fov"]
    spots = [c for c in viewer.candidates if fov is None or np.hypot(c["x"] - fov[0], c["y"] - fov[1]) < 0.6 * fov[2]]
    if not out.check("candidate spots inside the view", len(spots) >= 4, f"{len(spots)}"):
        return out
    for c in spots[:2]:  # two outlines being followed (one more each time ahead)
        _, header, _ = viewer.latest()
        since = len(viewer.results)
        await viewer.send({"type": "click", "frameId": header["id"], "x": c["x"], "y": c["y"], "identify": False})
        await viewer.result_for("click", since)
    await asyncio.sleep(2.0)
    since_cells = len(viewer.results)
    started = time.time()
    steps_before = ((await stack.orch_health()).get("follow") or {}).get("steps", 0)
    await viewer.send({"type": "cells"})
    admitted = await wait_started(stack, viewer, since_cells, started, model)
    out.observations.append({"kind": "cells-rpc-admission", **admitted})
    if not out.check("loaded cells RPC began before measured clicks", "error" not in admitted, str(admitted)):
        await viewer.result_for("cells", since_cells, timeout=180)
        return out
    times = []
    observations = []
    diagnostics = os.environ.get("MICROMIND_CLICK_DIAGNOSTICS") == "1"
    for c in (spots * 4)[:12]:
        _, header, _ = viewer.latest()
        since = len(viewer.results)
        sent_at = time.time()
        t0 = time.perf_counter()
        await viewer.send({"type": "click", "frameId": header["id"], "x": c["x"], "y": c["y"], "identify": False})
        reply = await viewer.result_for("click", since)
        elapsed = (time.perf_counter() - t0) * 1000
        if reply and reply.get("ok"):
            times.append(elapsed)
        observations.append({"sentAt": sent_at, "receivedAt": time.time(), "frameId": header["id"],
                             "ok": bool(reply and reply.get("ok")), "roundTripMs": round(elapsed, 3)})
        await asyncio.sleep(0.15)
    cells = await viewer.result_for("cells", since_cells, timeout=180)
    finished = time.time()
    times.sort()
    p95 = times[min(len(times) - 1, int(0.95 * len(times)))] if times else None
    out.check("the cell run finished", bool(cells and cells.get("ok")), f"{cells}")
    out.check("clicks answered while it ran", len(times) >= 10, f"{len(times)} of 12")
    # Where the time went, per click: the worker's own outline time (its execution line) against the round trip.
    worker_ms = [round(a.get("ms", 0)) for r in _interactions("execution") if r.get("origin") == "click"
                 for a in r.get("attempts", [])[:1]][-len(times):]
    out.check("click p95 under 300 ms during the cell run", p95 is not None and p95 < 300,
              f"p95 {p95 and round(p95)} ms (round trips {[round(t) for t in times]}; outline model {worker_ms} ms)")
    follow = (await stack.orch_health()).get("follow") or {}
    out.check("following went on beside it (its own worker answered steps during the run)",
              follow.get("steps", 0) > steps_before, f"{steps_before} -> {follow.get('steps')} steps")
    await asyncio.sleep(2.0)
    follow = (await stack.orch_health()).get("follow") or {}
    out.check("following is going on within 2 s after it", follow.get("lastStepAt", 0) >= finished,
              f"last step {follow.get('lastStepAt', 0) - finished:+.1f} s after the run")
    try:
        rpc = cell_rpc(await detail(stack, admitted["receipt"]), model) or {}
    except (httpx.HTTPError, ValueError):
        rpc = {}
    same_rpc = (rpc.get("rpcId"), rpc.get("worker")) == (admitted["rpc"].get("rpcId"), admitted["rpc"].get("worker"))
    overlapping = overlap(observations, rpc) if same_rpc else []
    out.observations.append({"kind": "cells-click-overlap", "receipt": admitted["receipt"],
                             "rpc": rpc, "clicks": observations, "fullyOverlappingClicks": overlapping,
                             "scope": "worker RPC reply observations; not GPU kernel timestamps"})
    out.check("at least 10 successful clicks fully overlapped the same loaded cells RPC", len(overlapping) >= 10,
              f"{len(overlapping)} of {len(observations)}; receipt {admitted['receipt']}")
    if diagnostics:
        from .click_diagnostics import collect
        print("  click diagnostics " + json.dumps(await collect(stack, observations, started, finished),
                                                  separators=(",", ":")), flush=True)
    return out


CELLS_MOVE_AT = 4330  # just before the recording's fastest pan (as `arena`)


async def _until(test, seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return True
        await asyncio.sleep(0.1)
    return bool(test())


async def _cells(stack: Stack, viewer: Viewer, out: Outcome) -> dict | None:
    """Segment every cell in view; the population as the studio received it, once shown."""
    since = len(viewer.results)
    await viewer.send({"type": "cells"})
    reply = await viewer.result_for("cells", since, timeout=120)
    if not out.check("the cell run made a population", bool(reply and reply.get("ok") and reply.get("count")), str(reply)[:200]):
        return None
    pid = reply["population"]
    came = await _until(lambda: viewer.population.get("id") == pid and viewer.layers and viewer.layers[-1][1].get("population") == pid)
    out.check("its population and the layer reached the studio", came, f"{viewer.population.get('id')} {reply.get('count')} members")
    return reply


async def scenario_cells_population(stack: Stack, viewer: Viewer) -> Outcome:
    """Contract 8 in the run's edition (commercial: EdgeTAM's automatic masks; research: Cellpose-SAM):
    every cell segmented is a member of a population, measured by the app from its own mask, shown
    member by member once checked in place, saved, and counted from the population; no cell is a
    scene object."""
    out = Outcome("cells are a population: measured, shown member by member, saved, counted")
    # A known view at rest: where the recording plays on, the phone's lens refocuses at times (the
    # eyepiece disc changes by 1-2%), and then members are rightly not drawn (contract 3: geometry).
    out.check("footage moved to a still view", await stack.seek(CELLS_MOVE_AT))
    await stack.http.post(f"{stack.bridge_url}/feed/pause", params={"paused": 1})
    await asyncio.sleep(1.5)  # the seek and the pause have both taken hold: one picture, held
    try:
        return await _cells_population(stack, viewer, out)
    finally:
        await stack.http.post(f"{stack.bridge_url}/feed/pause", params={"paused": 0})


async def _cells_population(stack: Stack, viewer: Viewer, out: Outcome) -> Outcome:
    ok, _ = await stack.wait(lambda h: h["state"] == "live" and h["motion"] and h["motion"]["tracked"], 15, "live")
    reply = await _cells(stack, viewer, out)
    if reply is None:
        return out
    pop = viewer.population
    commercial = licences.edition() == "commercial"
    made = (pop.get("madeBy") or {}).get("implementation")
    out.check("made by this edition's cell finder", made == ("edgetam-automask" if commercial else "cellpose-sam"), str(made))
    measured = [m for m in pop.get("members", []) if (m.get("measure") or {}).get("areaPx")]
    out.check("every member is measured from its own mask", len(measured) == len(pop.get("members", [])) > 0,
              f"{len(measured)} of {len(pop.get('members', []))}")
    verified = await _until(lambda: len(viewer.layers[-1][1].get("verified") or []) > 0)
    layer = viewer.layers[-1][1]
    out.check("members are checked in place and drawn", verified,
              f"{len(layer.get('verified') or [])} verified" + (f"; the rest: {layer.get('why')}" if layer.get("why") else ""))
    out.check("no cell is a scene object", not any(o.get("kind") == "cell" for o in viewer.scene.values()))
    saved = await _until(lambda: viewer.population.get("status") == "saved", 15)
    out.check("it is saved (atomically, then said)", saved, viewer.population.get("status", ""))
    since = len(viewer.results)
    await viewer.send({"type": "tool", "name": "count", "args": {"label": "cells"}})
    counted = await viewer.result_for("tool", since, timeout=20)
    out.check("counting them reads the population, no new run", bool(counted and counted.get("ok")
              and str(counted.get("summary", "")).startswith(f"{reply['count']} (")
              and f", {reply['population']}:" in str(counted.get("summary", ""))), str(counted)[:200])
    return out


def _population_display_templates(gray, members):
    """Templates for drawable contours only; original mask members/counts are unchanged."""
    templates = {}
    for mid, member in members.items():
        points = [point for polygon in member["polygons"] for point in polygon]
        if not points:
            continue  # a measured mask can have no contour above the display-area floor
        pts = np.array(points)
        x0, y0 = (pts.min(axis=0) - 8).astype(int)
        x1, y1 = (pts.max(axis=0) + 8).astype(int)
        if x0 >= 0 and y0 >= 0 and x1 - x0 >= 20 and y1 - y0 >= 20:
            templates[mid] = (gray[y0:y1, x0:x1].copy(), (x0, y0))
    return templates


async def scenario_cells_then_move(stack: Stack, viewer: Viewer) -> Outcome:
    """Contract 3 for members: cells segmented on a still view, then the slide pans fast. A member is
    drawn only while its own pixels are checked where the slide's move puts it: every verified member
    the studio draws is where its own pixels are (measured from the view it was made on), never
    stale; members drop out during the pan and come back once the view settles."""
    out = Outcome("after the slide moves, only members checked in place are drawn, and where they are")
    out.check("footage moved to the pan", await stack.seek(CELLS_MOVE_AT))
    await stack.http.post(f"{stack.bridge_url}/feed/pause", params={"paused": 1})  # a slide at rest while they are found
    await asyncio.sleep(1.5)  # the seek and the pause have both taken hold
    ok, _ = await stack.wait(lambda h: h["state"] == "live" and h["motion"] and h["motion"]["tracked"], 15, "live")
    viewer.keep_jpegs = True
    try:
        reply = await _cells(stack, viewer, out)
        if reply is None:
            return out
        await _until(lambda: len(viewer.layers[-1][1].get("verified") or []) > 0)
        pop = viewer.population
        frame = next((f for f in viewer.frames if f[1]["id"] == pop["capture"] and f[2]), None)
        if not out.check("the view they were made on was received", frame is not None, f"capture {pop['capture']}"):
            return out
        gray0 = cv2.imdecode(np.frombuffer(frame[2], np.uint8), cv2.IMREAD_GRAYSCALE)
        members = {m["id"]: m for m in pop["members"]}
        templates = _population_display_templates(gray0, members)
        await stack.http.post(f"{stack.bridge_url}/feed/pause", params={"paused": 0})  # the pan plays
        errors, drawn_during, seen, epochs, stale, off = [], [], 0, [], 0, []
        start = time.time()
        while time.time() - start < 12:
            await asyncio.sleep(0.4)
            _, header, jpeg = viewer.latest()
            epochs.append(header["epoch"])
            if not jpeg or header["epoch"] != pop["epoch"] or header.get("tracked", True) is False:
                # Continuity lost (a new epoch): no member may be verified for drawing once its check has
                # lapsed (the studio draws nothing of another epoch anyway).
                lost_for = sum(1 for e in epochs[-3:] if e != pop["epoch"])
                stale += lost_for >= 3 and bool(viewer.layers[-1][1].get("verified"))
                drawn_during.append(0)
                continue
            verified = set(viewer.layers[-1][1].get("verified") or [])
            # As the studio draws (contract/overlay.json): overlays fade out while the slide moves fast,
            # so only frames where they are fully shown are measured (as `arena` does).
            if overlay_opacity([h.get("speed", 0.0) for _, h, _ in viewer.frames[-SPEED_WINDOW:]]) < 0.99:
                drawn_during.append(len(verified))
                continue
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
            cx, cy = viewer.layers[-1][1].get("correction") or (0.0, 0.0)  # as the studio draws them (population.ts)
            sx, sy = header["dx"] - pop["offset"][0] + cx, header["dy"] - pop["offset"][1] + cy
            drawn_during.append(len(verified))
            for mid in list(verified & set(templates))[:12]:
                template, (tx, ty) = templates[mid]
                px, py = int(round(tx + sx)), int(round(ty + sy))
                th, tw = template.shape
                ax, ay = px - 20, py - 20
                if ax < 0 or ay < 0 or ax + tw + 40 > img.shape[1] or ay + th + 40 > img.shape[0]:
                    continue
                res = cv2.matchTemplate(img[ay:ay + th + 40, ax:ax + tw + 40], template, cv2.TM_CCOEFF_NORMED)
                _, score, _, loc = cv2.minMaxLoc(res)
                others = res.copy()
                r = max(4, min(tw, th) // 3)
                cv2.rectangle(others, (loc[0] - r, loc[1] - r), (loc[0] + r, loc[1] + r), -1.0, -1)
                if score > 0.6 and float(others.max()) <= score - MEASURE_CLEAR_MARGIN:  # an unambiguous match only
                    errors.append(float(np.hypot(loc[0] - 20, loc[1] - 20)))
                    seen += 1
                    if errors[-1] > 5:
                        off.append((mid, round(errors[-1], 1), loc[0] - 20, loc[1] - 20, round(header.get("speed", 0), 1),
                                    round(time.time() - start, 1), tw, th))
        lost = epochs and epochs[-1] != pop["epoch"]
        if off:
            print(f"  (off by more than 5 px: {off[:12]})", flush=True)
        out.check("no member is verified in a view that lost continuity", stale == 0, f"{stale} samples")
        if errors:
            out.check("a drawn member is where its pixels are: median <= 2 px, 95% <= 5 px",
                      np.median(errors) <= 2 and np.percentile(errors, 95) <= 5,
                      f"median {np.median(errors):.1f}, 95% {np.percentile(errors, 95):.1f}, max {max(errors):.1f} px")
        if lost:
            # The pan lost the slide's continuity this time: the members stay on their capture (never
            # drawn at an unchecked place) until the place is rejoined (motion.py's places seen before).
            print(f"  (continuity lost at the pan: epochs {sorted(set(epochs))}; {seen} samples before it)", flush=True)
        else:
            out.check("verified members were measured while drawn", seen >= 10, f"{seen} samples")
            # After the pan they come back once checked (what plays later may refocus the phone's lens,
            # and then they rightly go again: the geometry precondition).
            out.check("members are drawn again after the pan", bool(drawn_during) and max(drawn_during[len(drawn_during) // 2:]) > 0,
                      str(drawn_during))
    finally:
        viewer.keep_jpegs = False
        await stack.http.post(f"{stack.bridge_url}/feed/pause", params={"paused": 0})
    return out


async def scenario_evidence_restart(stack: Stack, viewer: Viewer) -> Outcome:
    """Contract 8, ADR 0001: the day's evidence is read back after a restart: the population comes back
    with its members and measurements, and is counted without segmenting again."""
    out = Outcome("the day's evidence comes back after a restart")
    ok, _ = await stack.wait(lambda h: h["state"] == "live" and h["motion"] and h["motion"]["tracked"], 15, "live")
    reply = await _cells(stack, viewer, out)
    if reply is None:
        return out
    if not out.check("saved before the restart", await _until(lambda: viewer.population.get("status") == "saved", 15),
                     viewer.population.get("status", "")):
        return out
    await stack.restart_orchestrator()
    again = Viewer(stack.ws_url)
    task = asyncio.create_task(again.run())
    try:
        await _until(lambda: hasattr(again, "ws"), 15)
        await asyncio.sleep(1.0)
        since = len(again.results)
        await again.send({"type": "tool", "name": "query_population", "args": {"of": reply["population"]}})
        found = await again.result_for("tool", since, timeout=20)
        out.check("the population is back, counted as it was", bool(found and found.get("ok")
                  and f"{reply['population']}" in str(found.get("summary")) and f"{reply['count']} instances" in str(found.get("summary"))),
                  str(found)[:240])
        # The session's one index has it too, read over its HTTP API as programs and researchers read it
        # (resources.py, Amendment 2.7): the same id, its title, and its view by reference.
        listing = (await stack.http.get(f"{stack.orch_url}/api/resources?kind=population")).json()
        entry = next((e for e in listing.get("entries", []) if e["id"] == reply["population"]), None)
        out.check("the index lists it after the restart", bool(entry and entry["title"].startswith("Cell outlines")),
                  str(entry)[:200])
        picture = await stack.http.get(f"{stack.orch_url}/api/resources/{reply['population']}/picture")
        out.check("its view is read back by reference", picture.status_code == 200
                  and picture.headers.get("content-type", "").startswith("image/jpeg"), str(picture.status_code))
    finally:
        task.cancel()
    return out


REFIND_START = 5935  # comes back near 6000, so the phone's refocusing at 6340-6460 plays after it


async def scenario_refind(stack: Stack, viewer: Viewer) -> Outcome:
    """W5: the slide jumps far away (continuity lost), then comes back to where an outline was
    made: the place is recognised, its epoch rejoined, and the outline drawn again in the
    right place, never misplaced. It starts at a fixed place in the recording, so that after the
    return it always plays the phone refocusing by itself (frames 6340-6460: the disc 399 -> 395 px,
    sharpness 206 -> 39) and the sharp view after it: outlines moved by the object follower during
    that hunt were 10-16 px off once it was sharp again (objtrack.ObjectFollower._clear).
    Before it seeked, it started wherever the scenarios before it left the recording, and failed
    only in full runs."""
    out = Outcome("an outline comes back when the slide returns to its place")
    out.check("footage moved to the start", await stack.seek(REFIND_START))
    events, epochs = {}, []  # epochs: (s, epoch, dx, dy) whenever the view's epoch changes

    async def watch():
        t0 = time.time()
        while time.time() - t0 < 31:
            _, h, _ = viewer.latest()
            if h and (not epochs or epochs[-1][1] != h.get("epoch")):
                epochs.append((round(time.time() - t0, 1), h.get("epoch"), round(h.get("dx", 0)), round(h.get("dy", 0))))
            await asyncio.sleep(0.1)

    async def away_and_back():
        t0 = time.time()  # the samples' clock starts with this
        asyncio.create_task(watch())
        await asyncio.sleep(2.0)
        status = (await stack.http.get(f"{stack.bridge_url}/sim/status")).json()
        here, total = status["worldFrame"], status["totalFrames"]
        events["here"] = here
        # Far away, at another magnification: nothing to bridge, so continuity is lost.
        events["cuts"] = [time.time() - t0]
        await stack.sim("degrade", zoom=2.5, motion=4)
        await stack.seek((here + total // 2) % total)
        await asyncio.sleep(7.0)
        events["back_at"] = time.time()
        events["cuts"].append(time.time() - t0)
        await stack.sim("degrade")
        await stack.seek(here + 30)  # back to the same place, a second later in the recording

    samples = await _outline_and_follow(stack, viewer, out, seconds=30, during=away_and_back)
    if not samples:
        return out
    back = events.get("back_at", time.time()) - (time.time() - samples[-1][0])
    after = [s for s in samples if s[0] > back + 2.0]
    # Drawn by the app (whether the harness can measure it then or not: its own template match
    # weakens as the recording's view changes; misplacement is the next check, on measured samples).
    visible = [s for s in after if s[2]]
    # Drawn and off by more than 10 px where it shows. Not counted: the 1.5 s after each cut, where
    # the motion tracker can accept the first frame of the new view before it declares the loss
    # (a known weakness of cuts, not of re-finding).
    near_cut = lambda t: any(0 <= t - c <= 1.5 for c in events.get("cuts", []))  # noqa: E731
    wrong = [s for s in samples if s[2] and s[3] and s[3][1] > 0.6 and s[3][0] > 10 and s[4] > 0.5 and not near_cut(s[0])]
    first = min((s[0] for s in samples if s[0] > back and s[2]), default=None)
    _, header, _ = viewer.latest()
    clicked = epochs[0][1] if epochs else None  # the epoch the outline was made in
    out.check("the place is recognised: the view rejoins the outline's epoch", header.get("epoch") == clicked,
              f"epoch {header.get('epoch')}, outline made in {clicked}")
    out.check("the outline is drawn again within 5 s of coming back", first is not None and first - back <= 5.0,
              f"{'never' if first is None else f'{first - back:.1f} s'}")
    out.check("it stays drawn", bool(visible) and len(visible) >= 0.8 * len(after), f"{len(visible)} of {len(after)} samples")
    out.check("never drawn in the wrong place", not wrong,
              f"{len(wrong)} bad" + (f", worst {max(s[3][0] for s in wrong):.0f} px" if wrong else ""))
    return out


async def scenario_identity(stack: Stack, viewer: Viewer) -> Outcome:
    """Session knowledge K2: two cells followed as individuals; clicks stay fast while they are measured; the
    slide jumps far away (continuity lost) and back to their place. While away, where they are is said to be
    unknown, with when the view left their epoch, never guessed; once the place is recognised, the epoch rejoined
    and their own pixels match, the same two individuals are where their outlines are, said from the slide's
    measured movement, with how they came back and how sure (never "seen continuously"). After a restart they are
    history: an earlier run of the app, never the new outlines that take their ids (review B1-B3 and SF10 of K2)."""
    import re

    out = Outcome("followed cells keep their individuals across a lost and rejoined view, and a restart")
    out.check("footage moved to the start", await stack.seek(REFIND_START))
    await stack.http.post(f"{stack.bridge_url}/feed/pause", params={"paused": 1})  # a slide at rest while they are found
    await asyncio.sleep(1.5)
    try:
        await stack.wait(lambda h: h["state"] == "live" and h["motion"] and h["motion"]["tracked"], 15, "live")
        reply = await _cells(stack, viewer, out)
    finally:
        await stack.http.post(f"{stack.bridge_url}/feed/pause", params={"paused": 0})
    if reply is None:
        return out

    async def tool(name, args, on=viewer):
        mark = len(on.results)
        await on.send({"type": "tool", "name": name, "args": args})
        return await on.result_for("tool", mark, timeout=30) or {}

    def lines_of(result) -> list[str]:
        return [line for line in (result.get("summary") or result.get("error") or "").splitlines() if line.strip()]

    # This journey starts from two visible outlines. Tiny/disconnected model masks
    # can remain valid population evidence without a drawable contour; choosing
    # the first two ids made the setup depend on those segmentation fragments.
    candidates = viewer.population.get("members", [])
    members = [m["id"] for m in candidates if m.get("polygons")][:2]
    excluded = [m["id"] for m in candidates if not m.get("polygons")]
    if not out.check("two drawable cells available for the following journey", len(members) == 2,
                     f"selected {members}; no drawable contour: {excluded}"):
        return out
    followed = await tool("follow_cells", {"members": members, "of": reply["population"]})
    said = followed.get("summary") or followed.get("error") or ""
    ids = re.findall(r"\bi\d+\b", said)
    if not out.check("two cells are followed as individuals", bool(followed.get("ok")) and len(ids) == 2, said):
        return out
    out.check("said in plain words, the individuals' ids last", said.startswith("keeping an eye on ")
              and not re.match(r"\S*i\d", said), said[:120])
    # Clicks stay fast while the followed cells are measured (the first round starts as they are followed).
    _, header, _ = viewer.latest()
    disc = header.get("fov")
    inner = [c for c in viewer.candidates if disc is None or np.hypot(c["x"] - disc[0], c["y"] - disc[1]) < 0.6 * disc[2]]
    times = []
    for c in (inner * 3)[:10]:
        _, header, _ = viewer.latest()
        since = len(viewer.results)
        t0 = time.perf_counter()
        await viewer.send({"type": "click", "frameId": header["id"], "x": c["x"], "y": c["y"]})
        answer = await viewer.result_for("click", since)
        if answer and answer.get("ok"):
            times.append((time.perf_counter() - t0) * 1000)
        await asyncio.sleep(0.3)
    times.sort()
    p95 = times[min(len(times) - 1, int(0.95 * len(times)))] if times else None
    out.check("click p95 under 300 ms while followed cells are measured", p95 is not None and p95 < 300 and len(times) >= 8,
              f"p95 {p95 and round(p95)} ms over {len(times)} clicks")
    from .identity_probe import followed_outlines, during_return

    named = await tool("individuals", {"ask": "where"})
    outlines = followed_outlines(reply["population"], members, ids, viewer.scene, named.get("summary") or "")
    if not out.check("each followed individual has its own unambiguous public outline",
                     bool(named.get("ok")) and len(outlines) == len(ids), str(outlines)):
        return out
    _, header, _ = viewer.latest()
    epoch0 = header.get("epoch")
    status = (await stack.http.get(f"{stack.bridge_url}/sim/status")).json()
    here, total = status["worldFrame"], status["totalFrames"]
    await stack.sim("degrade", zoom=2.5, motion=4)  # far away, at another magnification: continuity is lost
    await stack.seek((here + total // 2) % total)
    lost = await _until(lambda: viewer.latest()[1].get("epoch") != epoch0, 8)
    jumped = time.time()
    await asyncio.sleep(2.0)
    away = await tool("individuals", {"ask": "where"})
    words = away.get("summary") or away.get("error") or ""
    out.check("continuity was lost", lost, f"epoch {viewer.latest()[1].get('epoch')}, theirs {epoch0}")
    out.check("while away, where they are is said to be unknown, never guessed",
              all(f"({i})" in words for i in ids) and words.count("can't tell: continuity was lost") == 2, words[:240])
    told = [time.mktime(time.strptime(time.strftime("%Y-%m-%d ", time.localtime(jumped)) + t, "%Y-%m-%d %H:%M:%S"))
            for t in re.findall(r"continuity was lost at (\d\d:\d\d:\d\d)", words)]
    out.check("the loss is said at when the view left their epoch", bool(told) and all(abs(t - jumped) <= 3 for t in told),
              f"said {told}, the jump seen at {round(jumped)}")
    rejoined = False
    async def return_to_place(deadline):
        nonlocal rejoined
        await stack.sim("degrade")
        await stack.seek(here + 30)  # back to their place
        rejoined = await _until(lambda: viewer.latest()[1].get("epoch") == epoch0,
                               max(0.0, deadline - asyncio.get_running_loop().time()))
    # A tool reply can precede the next scene push. Match the retained scene to unchanged health revisions
    # across that exact query; start before returning so the epoch wait cannot hide the unverified interval.
    witnessed = await during_return(stack, viewer, outlines, return_to_place, seconds=15.0)
    out.check("the view rejoins their epoch", rejoined, f"epoch {viewer.latest()[1].get('epoch')}, theirs {epoch0}")
    out.observations.append({'kind': 'identity-return-transition', **witnessed['transition']})
    out.observations.extend(witnessed["samples"])
    words = witnessed["summary"]
    out.check("back at their place, the same individuals are where their outlines are, by their own pixels",
              witnessed["placed"], words[:240] or "no coherent verified placement within 15 s")
    coherent = sum(sample["coherent"] for sample in witnessed["samples"])
    out.check("never said to be in view while their own pixels had not matched", witnessed["passed"],
              f"{coherent}/{len(witnessed['samples'])} coherent samples; "
              f"witnessed refusal of at least one individual: {witnessed['refused']}; "
              f"invalid answers: {len(witnessed['violations'])}; outlines: {outlines}; "
              "a refusal of at least one individual must precede the verified placement of all within 15 s")
    if not out.check("identity observation completed", not witnessed["error"], witnessed["error"]):
        return out  # keep prior checks and the partial failing probe; do not replace them with a later transport error
    came_back = ("its place recognised when the slide came back", "re-found by its own pixels",
                 "picked up again by the object follower")
    lines = lines_of(await tool("individuals", {"ask": "where"}))
    out.check("each says how it came back and how sure (a carry, never 'seen continuously')",
              len(lines) == 2 and all(any(c in line for c in came_back) and "as sure as its weakest link" in line
                                      and "seen continuously (its outline followed)" not in line for line in lines),
              " | ".join(line[:120] for line in lines))
    # A restart: outline ids start again at o1 and epochs at 0; the individuals are history, never the new outlines.
    await stack.restart_orchestrator()
    again = Viewer(stack.ws_url)
    task = asyncio.create_task(again.run())
    try:
        await _until(lambda: hasattr(again, "ws"), 15)
        await asyncio.sleep(2.0)
        _, header, _ = again.latest()
        for c in (again.candidates or [])[:3]:  # new outlines, numbered from o1 again
            since = len(again.results)
            await again.send({"type": "click", "frameId": header["id"], "x": c["x"], "y": c["y"]})
            await again.result_for("click", since)
        lines = lines_of(await tool("individuals", {"ask": "where"}, on=again))
        out.check("after a restart they are history: an earlier run of the app, never placed",
                  len(lines) == 2 and all(f"({i})" in "\n".join(lines) for i in ids)
                  and all("in an earlier run of the app" in line and "in view now" not in line for line in lines),
                  " | ".join(line[:140] for line in lines))
    finally:
        task.cancel()
    return out


async def scenario_arena(stack: Stack, viewer: Viewer) -> Outcome:
    """Blind A/B/C: two local outline models answer one click; both stay locked on the
    specimen through a fast pan, names stay hidden, and a vote leaves only the winner."""
    out = Outcome("blind A/B/C outlines stay locked; the vote keeps only the winner")
    out.check("footage moved to the pan", await stack.seek(4330))  # just before the recording's fastest pan
    ok, _ = await stack.wait(lambda h: h["state"] == "live" and h["motion"] and h["motion"]["tracked"], 15, "live")
    await asyncio.sleep(1.0)
    since = len(viewer.results)
    await viewer.send({"type": "arena", "arena": {"enabled": True, "outline": ["edgetam", "sam2.1-tiny"]}})
    reply = await viewer.result_for("arena", since)
    if not out.check("blind mode turns on", reply and reply.get("ok"), str(reply)):
        return out
    try:
        viewer.keep_jpegs = True
        _, header, jpeg = viewer.latest()
        fov = header["fov"]
        inner = [c for c in viewer.candidates if fov is None or np.hypot(c["x"] - fov[0], c["y"] - fov[1]) < 0.6 * fov[2]]
        if not out.check("a candidate spot well inside the view", bool(inner)):
            return out
        target = sorted(inner, key=lambda c: abs(c["r"] - 8))[0]
        since = len(viewer.results)
        await viewer.send({"type": "click", "frameId": header["id"], "x": target["x"], "y": target["y"]})
        reply = await viewer.result_for("click", since, timeout=20)
        if not out.check("click starts a trial", reply and reply.get("ok") and reply.get("trialId"), str(reply)):
            return out
        trial = reply["trialId"]
        await asyncio.sleep(0.3)
        arms = {o["arm"]: o for o in viewer.scene.values() if o.get("trial") == trial}
        out.check("each option outlined the click", sorted(arms) == ["A", "B"], ", ".join(sorted(arms)))
        out.check("outlines don't reveal which model drew them",
                  all(o["source"] == "arena" and not o["note"] for o in arms.values()))
        gray = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
        templates = {}
        for letter, obj in arms.items():
            pts = np.array(obj["polygons"][0])
            x0, y0 = (pts.min(axis=0) - 10).astype(int)
            x1, y1 = (pts.max(axis=0) + 10).astype(int)
            templates[letter] = gray[max(0, y0) : y1, max(0, x0) : x1].copy()
        errors: dict[str, list[float]] = {letter: [] for letter in arms}
        states: dict[str, list] = {letter: [] for letter in arms}  # what each arm was, when it was not measured
        start = time.time()
        while time.time() - start < 15:
            await asyncio.sleep(0.5)
            _, header, jpeg = viewer.latest()
            opacity = overlay_opacity([h.get("speed", 0.0) for _, h, _ in viewer.frames[-SPEED_WINDOW:]])
            for letter in arms:
                obj = next((o for o in viewer.scene.values() if o.get("trial") == trial and o["arm"] == letter), None)
                states[letter].append((round(time.time() - start, 1), obj and obj["track"], obj and obj["anchorEpoch"],
                                       header["epoch"], round(opacity, 2)))
                if obj is None or not jpeg or opacity < 0.99:
                    continue
                if obj["track"] == "tracking" and obj["anchorEpoch"] == header["epoch"] and header.get("tracked", True) is not False and _inside_view(obj, header):
                    measured = _overlay_error(header, jpeg, obj, templates[letter])
                    if measured and measured[1] > 0.6:
                        errors[letter].append(measured[0])
        for letter, errs in errors.items():
            if out.check(f"outline {letter} measured while fully shown", len(errs) >= 8,
                         f"{len(errs)} samples" + ("" if len(errs) >= 8 else f"; (t, track, its epoch, epoch, opacity): {states[letter][::3]}")):
                out.check(f"outline {letter}: median <= 2 px, never over 5 px", np.median(errs) <= 2 and max(errs) <= 5,
                          f"median {np.median(errs):.1f}, max {max(errs):.1f} px")
        since = len(viewer.results)
        await viewer.send({"type": "vote", "trialId": trial, "winner": "B", "note": "harness", "by": "harness"})
        reply = await viewer.result_for("vote", since)
        if out.check("vote recorded", reply and reply.get("ok"), str(reply)):
            out.check("names revealed after the vote", set(r["option"] for r in reply["reveal"].values()) == {"edgetam", "sam2.1-tiny"})
            await asyncio.sleep(0.5)
            left = [o for o in viewer.scene.values() if o["id"] in {a["id"] for a in arms.values()}]
            out.check("only the winner remains, as an ordinary outline",
                      len(left) == 1 and left[0]["id"] == arms["B"]["id"] and left[0]["trial"] is None)
    finally:
        viewer.keep_jpegs = False
        await viewer.send({"type": "arena", "arena": {"enabled": False}})
    return out


def save_flight(name: str, flight: dict) -> None:
    """Write every frame the browser received (JPEG + header) for offline replay."""
    folder = RESULTS / f"flight-{name}"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.jpg"):
        old.unlink()
    headers = []
    for t, header, jpeg in flight["frames"]:
        (folder / f"{header['id']:07d}.jpg").write_bytes(jpeg)
        headers.append({**header, "receivedAt": t})
    (folder / "flight.json").write_text(json.dumps(
        {"headers": headers, **{key: value for key, value in flight.items() if key != "frames"}}, indent=1))
    print(f"  flight recording: {folder} ({len(headers)} frames)", flush=True)


async def scenario_ask_and_point(stack: Stack, viewer: Viewer) -> Outcome:
    """Cloud: a question is answered and the things it mentions are outlined on the view."""
    out = Outcome("ask a question, the AI answers and points (cloud)")
    await stack.seek(2650)  # a still view with two clear oval structures
    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    await asyncio.sleep(2.0)
    since = len(viewer.results)
    t0 = time.time()
    await viewer.send({"type": "ask", "text": "Highlight the oval structures"})
    reply = await viewer.result_for("ask", since, timeout=20)
    took = time.time() - t0
    if not out.check("question answered", reply and reply.get("ok"), str((reply or {}).get("error", ""))):
        return out
    out.check("answer within 5 s", took <= 5, f"{took:.1f} s (model {reply['thinkMs']} ms)")
    out.check("answer has text", len(reply.get("answer", "")) > 20, reply.get("answer", "")[:80])
    await asyncio.sleep(0.5)
    objects = [viewer.scene[i] for i in reply.get("objectIds", []) if i in viewer.scene]
    out.check("at least one thing outlined", len(objects) >= 1, f"{len(objects)} outlined")
    labelled = [o for o in objects if o["label"] and o["status"] == "likely"]
    out.check("outlines carry the model's label as a likely guess", len(labelled) == len(objects) and objects,
              ", ".join(f"{o['label']} ({o['confidence']})" for o in labelled))
    _, header, _ = viewer.latest()
    out.check("outlines are inside the eyepiece", all(_inside_view(o, header) for o in objects))

    # Clicking something names it on the outline and its card, without a chat post.
    chats_before = len(viewer.chats)
    _, header, _ = viewer.latest()
    spot = sorted(viewer.candidates, key=lambda c: abs(c["r"] - 8))[0] if viewer.candidates else None
    if out.check("a spot to click", spot is not None):
        since = len(viewer.results)
        await viewer.send({"type": "click", "frameId": header["id"], "x": spot["x"], "y": spot["y"]})
        clicked = await viewer.result_for("click", since)
        oid = (clicked or {}).get("objectId")
        end = time.time() + 15
        while time.time() < end and not (viewer.scene.get(oid) or {}).get("seen"):
            await asyncio.sleep(0.25)
        obj = viewer.scene.get(oid) or {}
        out.check("the click was named, with what was seen on its card", bool(obj.get("label") and obj.get("seen")),
                  f"{obj.get('label')!r}: {obj.get('seen', '')[:80]}")
        out.check("no chat post for the click", len(viewer.chats) == chats_before, f"{len(viewer.chats) - chats_before} posts")
    return out


async def scenario_voice(stack: Stack, viewer: Viewer, model: str = "") -> Outcome:
    """Cloud: talk to the voice model through /ws/voice the way the studio's Talk button
    does (synthesized speech as the microphone), and get speech back plus an outline."""
    import tempfile

    from bench.live_probe import tts

    out = Outcome(f"talk ({model or 'selected voice model'}): answers aloud and outlines what was asked about (cloud)")
    await stack.seek(2650)
    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    await asyncio.sleep(2.0)
    with tempfile.TemporaryDirectory() as tmp:
        speech = tts("What is the oval shape in the middle of the view?", Path(tmp))
    before = set(viewer.scene)
    events: list[tuple[float, dict]] = []
    audio_at: list[float] = []
    async with websockets.connect(stack.ws_url + "/voice" + (f"?model={model}" if model else ""), max_size=None) as ws:
        async def mic() -> None:
            chunk = 640  # 40 ms at 16 kHz, like the studio
            silence = np.zeros(chunk, np.int16).tobytes()
            for _ in range(25):  # a second of room tone first
                await ws.send(silence)
                await asyncio.sleep(0.04)
            for i in range(0, len(speech), chunk):
                await ws.send(speech[i : i + chunk].tobytes())
                await asyncio.sleep(0.04)
            events.append((time.time(), {"kind": "speech_end"}))
            while True:
                await ws.send(silence)
                await asyncio.sleep(0.04)

        async def listen() -> None:
            async for msg in ws:
                if isinstance(msg, bytes):
                    if msg[:4] == b"MMA1" and _audible(msg[4:]):
                        audio_at.append(time.time())
                else:
                    events.append((time.time(), json.loads(msg)))

        mic_task, listen_task = asyncio.create_task(mic()), asyncio.create_task(listen())
        outlined_at = None
        end = time.time() + 25
        while time.time() < end:
            await asyncio.sleep(0.25)
            kinds = [e["kind"] for _, e in events]
            new = [o for oid, o in viewer.scene.items() if oid not in before and _from_voice(o)]
            if new and outlined_at is None:
                outlined_at = time.time()
            # Done once the answer that follows the tool result has been spoken.
            result_at = next((t for t, e in events if e["kind"] == "tool_result"), None)
            spoke = [t for t in audio_at if (result_at or 0) < t]
            if new and spoke and spoke[-1] < time.time() - 1.5 and (result_at or "tool_call" not in kinds):
                break
        mic_task.cancel()
        listen_task.cancel()
    kinds = [e["kind"] for _, e in events]
    out.check("session opened with the selected voice model", "model" in kinds, ", ".join(dict.fromkeys(kinds)))
    spoken_end = next((t for t, e in events if e["kind"] == "speech_end"), None)
    heard = " ".join(e.get("text", "") for _, e in events if e["kind"] == "input_transcript" and not (e.get("data") or {}).get("interim"))
    out.check("the question was heard", "oval" in heard.lower(), heard[:80])
    first_audio = next((t for t in audio_at if spoken_end and t > spoken_end), None)
    if out.check("the model answered aloud", first_audio is not None):
        out.check("speech starts within 2.5 s of the question", first_audio - spoken_end <= 2.5, f"{first_audio - spoken_end:.2f} s")
    # It may answer from its view notes at once; then the app points on its behalf.
    out.check("the request reached the app (hand-off, or pointing on its behalf)", "tool_call" in kinds or bool(
        [o for oid, o in viewer.scene.items() if oid not in before and _from_voice(o)]))
    new = [o for oid, o in viewer.scene.items() if oid not in before and _from_voice(o)]
    when = f", {outlined_at - spoken_end:.1f} s after the question" if outlined_at and spoken_end else ""
    if out.check("something was outlined on the view", bool(new), ", ".join(o["label"] for o in new) + when):
        _, header, _ = viewer.latest()
        out.check("the outline is inside the eyepiece", all(_inside_view(o, header) for o in new))
    result_at = next((t for t, e in events if e["kind"] == "tool_result"), None)
    said = "".join(e.get("text", "") for _, e in events if e["kind"] == "output_transcript")
    answer = "".join(e.get("text", "") for t, e in events if e["kind"] == "output_transcript" and (not result_at or t > result_at))
    out.check("the model explains what it is", len(answer) > 20, answer[:120])
    out.check("the whole spoken reply has a transcript", len(said) > 20, said[:160])
    return out


def _audible(pcm: bytes) -> bool:
    """Model speech, not the silence GPT-Live streams between turns."""
    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], np.int16)
    return bool(len(samples)) and float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) > 100


def _from_voice(obj: dict) -> bool:
    # Gemini Live's locate tool outlines as "voice"; GPT-Live's delegation goes through the
    # chat agent, whose outlines carry the vision model ("ai:...").
    return obj["source"] == "voice" or obj["source"].startswith("ai:")


async def _outline_a_spot(stack: Stack, viewer: Viewer, out: Outcome, near: float = 0.6, radius: float = 8) -> str | None:
    """Click a mid-sized detected spot well inside the view; returns the new outline's id."""
    await stack.wait(lambda h: h["state"] == "live" and h["motion"] and h["motion"]["tracked"], 15, "live")
    await asyncio.sleep(1.0)
    _, header, _ = viewer.latest()
    fov = header["fov"]
    inner = [c for c in viewer.candidates if fov is None or np.hypot(c["x"] - fov[0], c["y"] - fov[1]) < near * fov[2]]
    if not out.check("a candidate spot well inside the view", bool(inner)):
        return None
    target = sorted(inner, key=lambda c: abs(c["r"] - radius))[0]
    since = len(viewer.results)
    await viewer.send({"type": "click", "frameId": header["id"], "x": target["x"], "y": target["y"], "identify": False})
    reply = await viewer.result_for("click", since, timeout=20)
    if not out.check("click produces an outline", reply and reply.get("ok"), str(reply)):
        return None
    return reply["objectId"]


async def scenario_magnifier(stack: Stack, viewer: Viewer) -> Outcome:
    """Zoom on an outline: the magnifier opens, its sharpest-frame crop is served at full
    resolution, focus can be judged, and closing it closes it."""
    out = Outcome("magnifier: zoom, sharpest frame at full resolution, focus check, close")
    await stack.seek(2650)
    object_id = await _outline_a_spot(stack, viewer, out)
    if object_id is None:
        return out
    since = len(viewer.results)
    await viewer.send({"type": "tool", "name": "zoom", "args": {"object": object_id, "hold": True}})
    reply = await viewer.result_for("tool", since)
    out.check("zoom tool runs", reply and reply.get("ok"), str(reply))
    await asyncio.sleep(0.3)
    mag = viewer.magnifier
    held = [s for s in mag.get("sources", []) if s["kind"] == "held"]
    out.check("magnifier opens on the object, showing the sharpest frame",
              mag.get("open") and mag.get("objectId") == object_id and mag["sources"][mag["index"]]["kind"] == "held")
    if held:
        r = await stack.http.get(stack.orch_url + held[0]["url"])
        img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR) if r.status_code == 200 else None
        obj = viewer.scene.get(object_id) or {}
        pts = np.array([p for poly in obj.get("polygons", []) for p in poly]) if obj else np.zeros((1, 2))
        bw = float(np.ptp(pts[:, 0])) if len(pts) else 0
        out.check("sharpest-frame crop is a full-resolution JPEG of the object",
                  img is not None and img.shape[1] >= bw, f"{r.status_code}, {None if img is None else img.shape[1::-1]}, object {bw:.0f} px wide, sharpness {r.headers.get('x-sharpness')}")
    # Closer, as asked: the level is in the magnifier's state (the brain and studio both see it),
    # the sources stay, and no level is refused (the closest is clamped, never an error).
    sources_before = [s["kind"] for s in mag.get("sources", [])]
    since = len(viewer.results)
    await viewer.send({"type": "tool", "name": "zoom", "args": {"level": "more"}})
    reply = await viewer.result_for("tool", since)
    await asyncio.sleep(0.3)
    out.check("\"more\" on the open magnifier doubles its level",
              bool(reply and reply.get("ok")) and viewer.magnifier.get("zoom") == 2
              and viewer.magnifier.get("objectId") == object_id
              and [s["kind"] for s in viewer.magnifier.get("sources", [])] == sources_before,
              f"{(reply or {}).get('summary')}; zoom {viewer.magnifier.get('zoom')}")
    since = len(viewer.results)
    await viewer.send({"type": "magnifier", "action": "level", "level": 1000})
    reply = await viewer.result_for("magnifier", since)
    await asyncio.sleep(0.3)
    out.check("the studio's level reaches the server, clamped to the closest it goes",
              bool(reply and reply.get("ok")) and 2 < (viewer.magnifier.get("zoom") or 0) <= 64,
              f"reply level {(reply or {}).get('level')}, zoom {viewer.magnifier.get('zoom')}")
    since = len(viewer.results)
    await viewer.send({"type": "tool", "name": "focus_check", "args": {"object": object_id}})
    reply = await viewer.result_for("tool", since)
    out.check("focus check answers", reply and reply.get("ok"), (reply or {}).get("summary", ""))
    since = len(viewer.results)
    await viewer.send({"type": "magnifier", "action": "close"})
    await viewer.result_for("magnifier", since)
    await asyncio.sleep(0.2)
    out.check("closing closes it", viewer.magnifier.get("open") is False)
    return out


async def scenario_slide_map(stack: Stack, viewer: Viewer) -> Outcome:
    """W5-4: the slide map holds the steady views seen (real pixels, served as a JPEG), the panel
    opens and closes as the brain's tool and the studio's button do, an outline's place on it is
    pasted pixels, and where_is answers from the offsets."""
    out = Outcome("slide map: steady views pasted, shown, where_is from the offsets")
    await stack.seek(2650)
    object_id = await _outline_a_spot(stack, viewer, out)
    if object_id is None:
        return out
    await asyncio.sleep(3.5)  # a still view is pasted again after REFRESH_S: the map holds the view now
    since = len(viewer.results)
    await viewer.send({"type": "tool", "name": "show_map", "args": {}})
    reply = await viewer.result_for("tool", since)
    out.check("show_map opens the map", bool(reply and reply.get("ok")), (reply or {}).get("summary", ""))
    end = time.monotonic() + 5
    while time.monotonic() < end and not (viewer.slidemap.get("open") and viewer.slidemap.get("url")):
        await asyncio.sleep(0.1)
    state = dict(viewer.slidemap)
    if not out.check("the studio is told it is open, with a map", bool(state.get("open") and state.get("url")), str(state)):
        return out
    r = await stack.http.get(stack.orch_url + state["url"])
    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR) if r.status_code == 200 else None
    out.check("the map is served as a JPEG of the size the studio was told",
              img is not None and list(img.shape[1::-1]) == state["size"], f"{r.status_code}, {None if img is None else img.shape}")
    obj = viewer.scene.get(object_id) or {}
    if img is not None and obj:
        pts = np.array([p for poly in obj["polygons"] for p in poly], dtype=float)
        cx, cy = (pts.min(axis=0) + pts.max(axis=0)) / 2
        mx = int((cx - obj["anchorOffset"][0]) * state["scale"] - state["origin"][0])
        my = int((cy - obj["anchorOffset"][1]) * state["scale"] - state["origin"][1])
        inside = 0 <= mx < img.shape[1] and 0 <= my < img.shape[0]
        out.check("the outline's place on the map is pasted pixels (its mark sits on the slide)",
                  inside and int(img[my, mx].sum()) > 30, f"map px ({mx}, {my}) of {img.shape[1::-1]}")
    since = len(viewer.results)
    await viewer.send({"type": "tool", "name": "where_is", "args": {"object": object_id}})
    reply = await viewer.result_for("tool", since)
    out.check("where_is: the outline just made is in view", bool(reply and reply.get("ok")) and "in view now" in reply["summary"],
              (reply or {}).get("summary", ""))
    since = len(viewer.results)
    await viewer.send({"type": "slidemap", "open": False})
    reply = await viewer.result_for("slidemap", since)
    await asyncio.sleep(0.3)
    out.check("the studio's close closes it", bool(reply and reply.get("ok")) and viewer.slidemap.get("open") is False,
              str(reply))
    return out


async def scenario_dissect_clicks(stack: Stack, viewer: Viewer) -> Outcome:
    """Cloud: a dissect runs (GPU regions + model naming) while the user keeps clicking;
    clicks stay fast because the GPU worker serves them between chunks."""
    out = Outcome("dissect runs while clicks stay fast (cloud)")
    await stack.seek(2650)
    object_id = await _outline_a_spot(stack, viewer, out, near=0.5, radius=40)
    if object_id is None:
        return out
    async def click_times(n: int) -> list[float]:
        times = []
        for _ in range(n):
            _, header, _ = viewer.latest()
            fov = header["fov"]
            x, y = (fov[0] + np.random.uniform(-0.4, 0.4) * fov[2], fov[1] + np.random.uniform(-0.4, 0.4) * fov[2]) if fov else (640, 360)
            mark = len(viewer.results)
            c0 = time.time()
            await viewer.send({"type": "click", "frameId": header["id"], "x": float(x), "y": float(y), "identify": False})
            await viewer.result_for("click", mark, timeout=10)
            times.append((time.time() - c0) * 1000)
            await asyncio.sleep(0.15)
        return times

    alone = await click_times(4)
    since = len(viewer.results)
    t0 = time.time()
    await viewer.send({"type": "tool", "name": "dissect", "args": {"object": object_id}})
    times = await click_times(6)
    reply = await viewer.result_for("tool", since, timeout=40)
    out.check("dissect finishes", reply is not None, (reply or {}).get("summary", "") or str((reply or {}).get("error")))
    out.check("dissect took under 15 s", reply is not None and time.time() - t0 < 15, f"{time.time() - t0:.1f} s")
    p95 = float(np.percentile(times, 95))
    out.check("clicks during dissect: p95 under 300 ms", p95 < 300, f"p95 {p95:.0f} ms, median {np.median(times):.0f} ms; " + ", ".join(f"{t:.0f}" for t in times) + "; alone " + ", ".join(f"{t:.0f}" for t in alone))
    return out


# The stack's own interaction log (Stack sets it; --attach reads the real one).
INTERACTIONS = Paths.from_env().interaction_log


def _logged_since(t: float, kinds: tuple[str, ...]) -> list[dict]:
    """Interaction-log entries of these kinds written since wall time t (the app's own record)."""
    path = INTERACTIONS
    out = []
    if not path.exists():
        return out
    for line in path.read_text().splitlines()[-200:]:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        stamp = time.mktime(time.strptime(entry.get("at", "1970-01-01T00:00:00"), "%Y-%m-%dT%H:%M:%S"))
        if entry.get("kind") in kinds and stamp >= int(t) - 1:
            out.append(entry)
    return out


def _actions_since(t: float, talk) -> dict[str, dict]:
    """What the app did for spoken requests since t: hand-off results and actions taken on
    hearing (hands), both count."""
    used = {}
    for r in talk.results_since(t):
        used.update(_tools_used((r.get("result") or {}).get("actions")))
    for entry in _logged_since(t, ("hands", "turn", "point")):
        if entry.get("channel", "voice") == "voice":
            used.update(_tools_used(entry.get("actions")))
    return used


def _tools_used(actions) -> dict[str, dict]:
    return {a["tool"]: a for a in actions or [] if isinstance(a, dict) and "tool" in a}


async def scenario_brain_chat(stack: Stack, viewer: Viewer) -> Outcome:
    """Cloud: typed requests use the brain's tools: "zoom in on" opens the magnifier on the
    thing, and "what is it made of?" dissects the same thing ("it" from the conversation)."""
    out = Outcome("chat drives tools: zoom, then dissect \"it\" (cloud)")
    await stack.seek(2650)
    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    await asyncio.sleep(2.0)
    since = len(viewer.results)
    t0 = time.time()
    await viewer.send({"type": "ask", "text": "Zoom in on the oval in the middle"})
    reply = await viewer.result_for("ask", since, timeout=25)
    took = time.time() - t0
    if not out.check("question answered", reply and reply.get("ok"), str((reply or {}).get("error", ""))):
        return out
    used = _tools_used(reply.get("actions"))
    out.check("answer within 6 s", took <= 6, f"{took:.1f} s; tools {', '.join(used) or 'none'}")
    out.check("the zoom tool ran", used.get("zoom", {}).get("ok") is True, str(used.get("zoom")))
    await asyncio.sleep(0.3)
    zoomed = viewer.magnifier.get("objectId")
    out.check("the magnifier opened on what the answer outlined",
              viewer.magnifier.get("open") and zoomed in (reply.get("objectIds") or []),
              f"magnifier on {zoomed}, answer outlined {reply.get('objectIds')}")
    chats_before = len(viewer.chats)
    updates_before = len(viewer.action_updates)
    since = len(viewer.results)
    await viewer.send({"type": "ask", "text": "What is it made of?"})
    reply = await viewer.result_for("ask", since, timeout=25)
    if not out.check("follow-up answered", reply and reply.get("ok"), str((reply or {}).get("error", ""))):
        return out
    used = _tools_used(reply.get("actions"))
    out.check("the dissect tool ran", "dissect" in used, ", ".join(used) or "no tools")
    # dissect may finish after the answer; then the answer's own action line updates (no new post)
    end = time.time() + 20
    late = None
    while time.time() < end and used.get("dissect", {}).get("ok") is None:
        late = next((c for c in viewer.action_updates[updates_before:] if c.get("tool") == "dissect"), None)
        if late:
            break
        await asyncio.sleep(0.25)
    result = used["dissect"]["summary"] if used.get("dissect", {}).get("ok") is True else (late or {}).get("summary", "")
    out.check("dissect reported its parts (or that none are visible)", bool(result), result[:140])
    out.check("no extra chat post for it", len([c for c in viewer.chats[chats_before:] if c.get("role") == "assistant"]) == 1,
              f"{len(viewer.chats) - chats_before} chat messages")
    out.check("\"it\" meant the zoomed object", viewer.magnifier.get("objectId") == zoomed or
              any(o.get("parent") == zoomed for o in viewer.scene.values()),
              f"magnifier on {viewer.magnifier.get('objectId')}, zoomed {zoomed}")
    return out


class Talk:
    """A voice session through /ws/voice, speaking synthesized questions like the studio's
    Talk button (room tone between them) and recording what comes back."""

    def __init__(self, stack: Stack) -> None:
        self.stack = stack
        self.events: list[tuple[float, dict]] = []
        self.audio_at: list[float] = []
        self.queue: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self) -> "Talk":
        self.ws = await websockets.connect(self.stack.ws_url + "/voice", max_size=None)
        self.tasks = [asyncio.create_task(self._mic()), asyncio.create_task(self._listen())]
        return self

    async def __aexit__(self, *exc) -> None:
        if os.environ.get("MICROMIND_TALK_DEBUG"):
            t0 = self.events[0][0] if self.events else 0
            for at, e in self.events:
                if e["kind"] not in ("audio",):
                    print(f"    {at - t0:6.2f} {e['kind']:18} {str(e.get('text') or e.get('result') or e.get('args') or e.get('data') or '')[:150]}")
            if self.audio_at:
                print(f"    audio from {self.audio_at[0] - t0:.2f} to {self.audio_at[-1] - t0:.2f}")
        for t in self.tasks:
            t.cancel()
        await self.ws.close()

    async def _mic(self) -> None:
        chunk = 640  # 40 ms at 16 kHz, like the studio
        silence = np.zeros(chunk, np.int16).tobytes()
        while True:
            try:
                speech, done = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                await self.ws.send(silence)
                await asyncio.sleep(0.04)
                continue
            for i in range(0, len(speech), chunk):
                await self.ws.send(speech[i : i + chunk].tobytes())
                await asyncio.sleep(0.04)
            done.set_result(time.time())

    async def _listen(self) -> None:
        async for msg in self.ws:
            if isinstance(msg, bytes):
                if msg[:4] == b"MMA1" and _audible(msg[4:]):
                    self.audio_at.append(time.time())
            else:
                self.events.append((time.time(), json.loads(msg)))

    async def say(self, text: str) -> float:
        """Speak; returns when the speech ended."""
        import tempfile

        from bench.live_probe import tts

        with tempfile.TemporaryDirectory() as tmp:
            speech = tts(text, Path(tmp))
        done = asyncio.get_running_loop().create_future()
        await self.queue.put((speech, done))
        return await done

    def results_since(self, t: float) -> list[dict]:
        return [e for at, e in self.events if at >= t and e["kind"] == "tool_result"]

    def said_since(self, t: float) -> str:
        return "".join(e.get("text", "") for at, e in self.events if at >= t and e["kind"] == "output_transcript")

    async def quiet(self, since: float, timeout: float = 25, after_result: bool = True) -> None:
        """Wait until the model has answered (after a tool result, if asked) and gone quiet."""
        end = time.time() + timeout
        while time.time() < end:
            await asyncio.sleep(0.25)
            results = [at for at, e in self.events if at >= since and e["kind"] == "tool_result"]
            if after_result and not results:
                continue
            start = results[-1] if after_result else since
            spoke = [t for t in self.audio_at if t > start]
            if spoke and spoke[-1] < time.time() - 1.5:
                return


async def scenario_voice_tools(stack: Stack, viewer: Viewer) -> Outcome:
    """Cloud: by voice, "zoom in on the oval" then "what is it made of?" use the same tools
    as chat (GPT-Live delegates to the brain), and the result is spoken."""
    out = Outcome("voice drives tools: zoom, then dissect \"it\" (cloud)")
    await stack.seek(2650)
    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    await asyncio.sleep(2.0)
    async with Talk(stack) as talk:
        await asyncio.sleep(1.0)
        t1 = await talk.say("Zoom in on the oval in the middle of the view.")
        await talk.quiet(t1, after_result=False)
        used = _actions_since(t1, talk)
        out.check("the request reached the brain and zoom ran", used.get("zoom", {}).get("ok") is True,
                  ", ".join(used) or "no tool results")
        zoomed = viewer.magnifier.get("objectId")
        out.check("the magnifier is open", bool(viewer.magnifier.get("open")), str(zoomed))
        out.check("it said something after zooming", len(talk.said_since(t1)) > 10, talk.said_since(t1)[:120])
        t2 = await talk.say("What is it made of?")
        await talk.quiet(t2, timeout=35, after_result=False)
        used = _actions_since(t2, talk)
        out.check("dissect ran on the voice request", "dissect" in used, ", ".join(used) or "no tool results")
        if used.get("dissect", {}).get("ok") is None:
            # still working when the brain answered: its result is reported (and spoken) later
            heard = len(talk.said_since(t2))
            end = time.time() + 25
            while time.time() < end and len(talk.said_since(t2)) == heard:
                await asyncio.sleep(0.5)
            await talk.quiet(time.time() - 0.5, timeout=15, after_result=False)
        out.check("\"it\" meant the zoomed object", viewer.magnifier.get("objectId") == zoomed or
                  any(o.get("parent") == zoomed for o in viewer.scene.values()),
                  f"magnifier on {viewer.magnifier.get('objectId')}, zoomed {zoomed}")
        out.check("the parts (or their absence) were spoken", len(talk.said_since(t2)) > 20, talk.said_since(t2)[:160])
    return out


async def scenario_cross_channel(stack: Stack, viewer: Viewer) -> Outcome:
    """Cloud: typing while a voice session is open gets a chat reply without the voice
    reading it out, and the voice knows what was typed (one conversation)."""
    out = Outcome("one conversation across chat and voice (cloud)")
    await stack.seek(2650)
    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    await asyncio.sleep(2.0)
    async with Talk(stack) as talk:
        await asyncio.sleep(2.0)
        since = len(viewer.results)
        await viewer.send({"type": "ask", "text": "Highlight the oval structures"})
        reply = await viewer.result_for("ask", since, timeout=25)
        if not out.check("typed question answered in the chat", reply and reply.get("ok"), str((reply or {}).get("error", ""))):
            return out
        answered = time.time()
        await asyncio.sleep(5.0)
        spoken = [t for t in talk.audio_at if t >= answered - 1]
        out.check("the voice stayed quiet about the typed answer", not spoken,
                  f"{len(spoken)} audio chunks; said: {talk.said_since(answered - 1)[:100]}")
        t = await talk.say("What did I just ask you to highlight?")
        await talk.quiet(t, after_result=False)
        said = talk.said_since(t)
        labels = {w for o in viewer.scene.values() for w in (o.get("label") or "").lower().split() if len(w) > 3}
        out.check("the voice knows what was typed", "oval" in said.lower() or any(w in said.lower() for w in labels), said[:160])
    return out


async def scenario_threads(stack: Stack, viewer: Viewer) -> Outcome:
    """Conversations within the day: start one, name it, go back to an earlier one, and after a
    restart of the orchestrator the same conversation is active, with the same list (no cloud)."""
    out = Outcome("conversations within the day survive a restart (no cloud)")
    if getattr(stack, "attach", False):
        out.check("needs its own stack (not --attach)", False)
        return out

    async def until(test, seconds=5.0):
        end = time.time() + seconds
        while time.time() < end and not test():
            await asyncio.sleep(0.05)
        return test()

    first = viewer.day.get("active")
    out.check("the day and its conversation arrive on connect", bool(first) and viewer.thread.get("thread") == first,
              f"day {viewer.day.get('id')}, active {first}")
    await viewer.send({"type": "thread", "action": "new", "title": "Harness: onion cells"})
    made = await until(lambda: viewer.day.get("active") not in (None, first))
    second = viewer.day.get("active")
    out.check("a new conversation starts on request and becomes active", made and viewer.thread.get("thread") == second,
              f"{first} -> {second}")
    await viewer.send({"type": "thread", "action": "rename", "id": second, "title": "Harness: onion peel"})
    named = await until(lambda: any(t["id"] == second and t["title"] == "Harness: onion peel" for t in viewer.day.get("threads", [])))
    out.check("it can be renamed", named, str([t["title"] for t in viewer.day.get("threads", [])]))
    await viewer.send({"type": "thread", "action": "switch", "id": first})
    back = await until(lambda: viewer.day.get("active") == first and viewer.thread.get("thread") == first)
    out.check("going back to the earlier one replays it", back, f"active {viewer.day.get('active')}")
    await viewer.send({"type": "thread", "action": "switch", "id": second})
    await until(lambda: viewer.day.get("active") == second)
    before = [(t["id"], t["title"]) for t in viewer.day.get("threads", [])]
    await stack.restart_orchestrator()
    again = Viewer(stack.ws_url)
    task = asyncio.create_task(again.run())
    try:
        came = await until(lambda: bool(again.day) and bool(again.thread), 15)
        after = [(t["id"], t["title"]) for t in again.day.get("threads", [])]
        out.check("after a restart the same conversation is active, with the same list",
                  came and again.day.get("active") == second and after == before, f"{again.day.get('active')}, {after}")
    finally:
        task.cancel()
    return out


LEGACY_DAY = "2026-10-05"  # a day journal from before sessions: it IS a session whose id is its date


def _seed_legacy_day(home: Path) -> None:
    """A day's journal and a bookmark as the app wrote them before sessions existed (no header, no session tags)."""
    at = time.mktime(time.strptime(f"{LEGACY_DAY} 15:00", "%Y-%m-%d %H:%M"))
    folder = home / "sessions" / LEGACY_DAY
    folder.mkdir(parents=True, exist_ok=True)
    events = [{"type": "thread-opened", "thread": "t1", "n": 1, "title": "Harness: old onion", "at": at},
              {"type": "subject", "thread": "t1", "name": "onion epidermis", "named_by": "user", "at": at + 1},
              {"type": "turn", "thread": "t1", "id": 1, "role": "user", "channel": "chat", "text": "Harness: what is this?",
               "frame_id": None, "refs": {}, "at": at + 2},
              {"type": "turn", "thread": "t1", "id": 2, "role": "assistant", "channel": "chat",
               "text": "Harness: onion epidermis cells.", "frame_id": None, "refs": {}, "at": at + 3}]
    (folder / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    marks = home / "bookmarks" / LEGACY_DAY
    marks.mkdir(parents=True, exist_ok=True)
    (marks / "b1.json").write_text(json.dumps({"id": "b1", "note": "Harness: the old onion spot", "at": at + 4,
                                               "thread": "t1", "subject": "onion epidermis", "run": "an-earlier-run"}))


async def scenario_sessions(stack: Stack, viewer: Viewer) -> Outcome:
    """Sessions are the project, not the day (ADR 0017), with no cloud: the session, the day and /api/sessions agree;
    an outline, then a rename; a new session clears the view to one empty conversation and lists the one left with its
    counts; a new conversation there leaves the first untouched; a scripted researcher started in the first session
    finishes in it after a switch; going back replays it with one resume line and no outlines; a restart comes back to
    the same session with the resume line once, and a second viewer sees nothing new; a day journal from before
    sessions is listed and opens with its conversations and its bookmark; and midnight does nothing."""
    out = Outcome("sessions: new, rename, switch, a researcher's session, a restart, an old day, midnight (no cloud)")
    if getattr(stack, "attach", False):
        out.check("needs its own stack (not --attach)", False)
        return out
    home = Path(stack.paths.home)
    _seed_legacy_day(home)

    async def listed() -> dict:
        return (await stack.http.get(f"{stack.orch_url}/api/sessions")).json()

    def row(sid: str, listing: dict | None = None) -> dict:
        return next((r for r in (listing or viewer.sessions).get("sessions", []) if r["id"] == sid), {})

    def journal(sid: str) -> list[dict]:
        return _jsonl(home / "sessions" / sid / "journal.jsonl")

    def resumed(lines: list[dict], sid: str) -> list[dict]:
        return [line for line in lines if line.get("type") == "app-notice"
                and line.get("producer") == {"kind": "session", "id": sid} and line.get("event") == "resumed"]

    async def switch(sid: str, to: Viewer | None = None) -> bool:
        to = to or viewer
        await to.send({"type": "session", "action": "switch", "id": sid})
        return await _until(lambda: to.session.get("id") == sid and to.day.get("id") == sid
                            and to.thread.get("day") == sid, 10)

    # The session, the day and the list agree.
    await _until(lambda: bool(viewer.sessions), 5)
    first = viewer.session.get("id")
    listing = await listed()
    out.check("the session, the day and /api/sessions agree", bool(first) and first == viewer.day.get("id")
              == listing.get("current") == viewer.sessions.get("current"),
              f"{first}, {viewer.day.get('id')}, {listing.get('current')}, {viewer.sessions.get('current')}")
    out.check("a day journal from before sessions is listed", bool(row(LEGACY_DAY, listing)),
              str([r["id"] for r in listing.get("sessions", [])]))
    # An outline, then a name.
    oid = await _outline_a_spot(stack, viewer, out)
    await viewer.send({"type": "session", "action": "rename", "id": first, "name": "Harness: leaf A"})
    out.check("renamed, the chip and the list say its name", await _until(
        lambda: viewer.session.get("name") == "Harness: leaf A" and viewer.session.get("named")
        and row(first).get("name") == "Harness: leaf A", 5), f"{viewer.session.get('name')}, {row(first).get('name')}")
    # A short Record here: its camera video is this session's recording (the view follows the session).
    recorded = await _record_briefly(stack, viewer, out)
    # A new session: the view cleared, one empty conversation, the one left listed with its counts.
    cleared = viewer.cleared
    held = row(first).get("counts", {}).get("conversations") or len(viewer.day.get("threads", []))  # earlier scenarios' too
    await viewer.send({"type": "session", "action": "new", "name": "Harness: B"})
    opened = await _until(lambda: viewer.session.get("id") not in (None, first) and viewer.thread.get("day") == viewer.session.get("id"), 10)
    second = viewer.session.get("id")
    out.check("a new session opens with a new id", opened and second != first, f"{first} -> {second}")
    out.check("its view is cleared, with one empty conversation",
              viewer.cleared > cleared and oid not in viewer.scene and len(viewer.day.get("threads", [])) == 1
              and not viewer.thread.get("lines"), f"cleared {viewer.cleared - cleared}, scene {list(viewer.scene)}, "
              f"{len(viewer.day.get('threads', []))} conversations, {len(viewer.thread.get('lines') or [])} lines")
    await _until(lambda: bool(row(first)) and viewer.sessions.get("current") == second, 5)
    out.check("the session left is listed with its counts", row(first).get("counts", {}).get("conversations") == held
              and row(first).get("name") == "Harness: leaf A", f"{held} conversations before: {row(first)}")
    out.check("session-cleared says the session opened, the one left and why",
              viewer.last_cleared == {"type": "session-cleared", "session": second, "previous": first, "why": "new"},
              str(viewer.last_cleared))
    if recorded:
        await _look_back_at(stack, viewer, out, first, second, recorded)
    # A new conversation in it leaves the first session's untouched.
    await viewer.send({"type": "thread", "action": "new", "title": "Harness: B two"})
    await _until(lambda: len(viewer.day.get("threads", [])) == 2, 5)
    listing = await listed()
    out.check("a new conversation there leaves the first session's untouched",
              row(second, listing).get("counts", {}).get("conversations") == 2
              and row(first, listing).get("counts", {}).get("conversations") == held, f"{row(second, listing).get('counts')}, "
              f"{row(first, listing).get('counts')}")
    # A scripted researcher started in the first session keeps working after a switch, and finishes in it.
    if await switch(first):
        since = len(viewer.results)
        await viewer.send({"type": "tool", "brief": "harness check (scripted)", "effort": "quick",
                           "fake": {"seconds": 3, "finding": "scripted finding"}})
        reply = await viewer.result_for("tool", since)
        rid = (reply or {}).get("rid")
        await switch(second)
        done = await _until(lambda: viewer.research.get(rid, {}).get("status") == "done", 20)
        await asyncio.sleep(0.5)
        theirs = [e for e in journal(first) if e.get("type") == "line" and rid and rid in json.dumps(e["line"])]
        ours = [e for e in journal(second) if e.get("type") == "line" and rid and rid in json.dumps(e["line"])]
        # A line filed again whole (its running then done form: the same lineId) is one line.
        ours_lines = {e["line"].get("lineId") or json.dumps(e["line"], sort_keys=True) for e in ours}
        out.check("a researcher keeps working after a switch and files in its own session; the other gets one line",
                  done and bool(theirs) and len(ours_lines) <= 1, f"{rid}: done {done}, {len(theirs)} line(s) in its session, "
                  f"{len(ours_lines)} in the one open: "
                  + "; ".join(f"{e['line'].get('type')}/{e['line'].get('event')}/{e['line'].get('lineId')}" for e in ours))
    else:
        out.check("went back to the first session", False, str(viewer.session.get("id")))
    # Back to the first: its conversation replayed, one resume line, no outlines.
    notices = viewer.notice_count
    back = await switch(first)
    await asyncio.sleep(0.3)
    told = resumed([n for c, n in viewer.notices if c > notices], first)
    out.check("going back replays it with one resume line and no outlines",
              back and len(told) == 1 and told[0] in (viewer.thread.get("lines") or []) and oid not in viewer.scene
              and "live view starts fresh" in told[0]["summary"], f"{len(told)} resume line(s), scene {list(viewer.scene)}")
    # A restart: the same session, the resume line once; a second viewer sees nothing new.
    before = len(resumed([e["line"] for e in journal(first) if e.get("type") == "line"], first))
    shown_before = len(resumed(viewer.thread.get("lines") or [], first))  # in its conversation, earlier scenarios' too
    await stack.restart_orchestrator()
    again, third = Viewer(stack.ws_url), Viewer(stack.ws_url)
    tasks = [asyncio.create_task(again.run())]
    try:
        came = await _until(lambda: bool(again.session) and bool(again.thread), 15)
        filed = len(resumed([e["line"] for e in journal(first) if e.get("type") == "line"], first))
        out.check("after a restart the same session, the resume line once",
                  came and again.session.get("id") == first and filed == before + 1
                  and len(resumed(again.thread.get("lines") or [], first)) == shown_before + 1, f"{again.session.get('id')}, "
                  f"{filed - before} filed, {len(resumed(again.thread.get('lines') or [], first))} replayed")
        tasks.append(asyncio.create_task(third.run()))
        await _until(lambda: bool(third.thread), 10)
        out.check("a second viewer sees nothing new", third.thread.get("lines") == again.thread.get("lines") and len(
            resumed([e["line"] for e in journal(first) if e.get("type") == "line"], first)) == filed)
        # The day journal from before sessions opens with its conversations and its bookmark.
        if await switch(LEGACY_DAY, again):
            marks = (await stack.http.get(f"{stack.orch_url}/api/resources", params={"kind": "bookmark"})).json()
            out.check("the old day opens with its conversations and its bookmark",
                      any(t.get("title") == "Harness: old onion" for t in again.day.get("threads", []))
                      and "Harness: onion epidermis cells." in json.dumps(again.thread.get("lines"))
                      and any(e.get("id") == "b1" for e in marks.get("entries", [])),
                      f"{[t.get('title') for t in again.day.get('threads', [])]}, "
                      f"{[e.get('id') for e in marks.get('entries', [])]}")
        else:
            out.check("the old day opens", False, str(again.session.get("id")))
        # Midnight does nothing: the store's clock is moved to just before the next one (only under MICROMIND_HOME).
        await switch(first, again)
        from .outline_fixture import held_outlines

        async with held_outlines(stack, again) as targets:
            _, header, _ = again.latest()
            since = len(again.results)
            await again.send({"type": "click", "frameId": header["id"], **targets[0], "identify": False})
            reply = await again.result_for("click", since, timeout=20)
            if not out.check("the labelled structure has an outline before midnight", bool(reply and reply.get("ok")), str(reply)):
                return out
            mark = reply["objectId"]
            today = time.strftime("%Y-%m-%d")
            midnight = time.mktime(time.strptime(today, "%Y-%m-%d")) + 86400
            (home / "clock-offset").write_text(str(midnight - 2.0 - time.time()))
            cleared = again.cleared
            await asyncio.sleep(3.5)  # the clock is read again at most twice a second
            await again.send({"type": "thread", "action": "new", "title": "Harness: after midnight"})
            await _until(lambda: len(again.session.get("days") or []) >= 2, 6)
            out.check("crossing midnight: no clearing, the same session, the outline stays, a second day after the next event",
                      again.cleared == cleared and again.session.get("id") == first and mark in again.scene
                      and len(again.session.get("days") or []) == 2, f"cleared {again.cleared - cleared}, "
                      f"{again.session.get('id')}, days {again.session.get('days')}, outline {mark in again.scene}")
    finally:
        (home / "clock-offset").unlink(missing_ok=True)
        for task in tasks:
            task.cancel()
    return out


async def _record_briefly(stack: Stack, viewer: Viewer, out: Outcome) -> str | None:
    """Record on for a few seconds and off again, as the studio's Record does (commands.py `dataset`): the dataset's
    id once its camera video is linked in, else None (said)."""
    since = len(viewer.results)
    await viewer.send({"type": "dataset", "on": True})
    on = await viewer.result_for("dataset", since)
    did = str(((on or {}).get("dataset") or {}).get("id") or "")
    video = stack.paths.datasets / did / "video" / "part-1.mp4"
    linked = bool(did) and await _until(lambda: video.exists() and video.stat().st_size > 20_000, 15)
    await asyncio.sleep(2.0)
    since = len(viewer.results)
    await viewer.send({"type": "dataset", "on": False})
    off = await viewer.result_for("dataset", since)
    ok = linked and bool(off and off.get("ok") and not off["dataset"]["on"])
    out.check("a short Record in the first session keeps its camera video", ok,
              f"{did}: {video.stat().st_size if video.exists() else 0} bytes, stopped {bool(off and off.get('ok'))}")
    return did if ok else None


async def _look_back_at(stack: Stack, viewer: Viewer, out: Outcome, first: str, second: str, did: str) -> None:
    """The view follows the session, the server's half: the first session's recordings list its Record and the new
    one's do not; the bridge plays it held at its start; while the studio holds the view (a new session's home) the running
    look makes no looks, and once released it looks again; then the sim's own feed is put back (never `source=camera`,
    which would open a real device)."""
    async def recordings(sid: str) -> list[dict]:
        return (await stack.http.get(f"{stack.orch_url}/api/sessions/{sid}/recordings")).json().get("recordings", [])

    theirs, ours = await recordings(first), await recordings(second)
    mine = [r for r in theirs if r.get("dataset") == did]
    out.check("the first session's recordings list its Record, the new session's do not",
              [r.get("part") for r in mine] == [1] and not any(r.get("dataset") == did for r in ours)
              and (mine[0].get("seconds") or 0) > 0, f"{theirs} / {ours}")
    before = (await stack.http.get(f"{stack.bridge_url}/feed")).json()
    played = await stack.http.post(f"{stack.bridge_url}/feed", params={"source": "recording", "dataset": did, "part": 1,
                                                                        "paused": 1}, timeout=30)
    feed = (await stack.http.get(f"{stack.bridge_url}/feed")).json()
    out.check("the bridge plays it held at its start, as footage",
              played.status_code == 200 and feed.get("source") == "footage" and feed.get("footage") == f"recording:{did}/1"
              and (feed.get("playback") or {}).get("paused") is True and (feed.get("playback") or {}).get("seconds") == 0,
              f"{played.status_code}: {feed.get('footage')}, {feed.get('playback')}")
    live, _ = await stack.wait(lambda h: h["state"] == "live", 15, "live on the recording")
    t0 = time.time()
    await asyncio.sleep(2.0)
    out.check("held, its picture keeps coming (a slide at rest)", live and len(viewer.frames_since(t0)) >= 20,
              f"{len(viewer.frames_since(t0))} frames in 2 s")

    async def looks() -> int | None:
        return ((await stack.orch_health()).get("sight") or {}).get("looks")

    await viewer.send({"type": "view", "held": True})
    held, _ = await stack.wait(lambda h: h.get("viewHeld") is True, 5, "the view held")
    start = await looks()
    await asyncio.sleep(4.0)
    end = await looks()
    out.check("while the view is held the running look makes no looks", held and start is not None and end == start,
              f"held {held}, looks {start} -> {end}")
    await viewer.send({"type": "view", "held": False})
    released, _ = await stack.wait(lambda h: h.get("viewHeld") is False, 5, "the view released")
    again = False
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not again:
        again = ((await looks()) or 0) > (end or 0)
        await asyncio.sleep(0.25)
    out.check("released, it looks again", released and again, f"released {released}, looks {end} -> {await looks()}")
    # Put back where it was, as the studio restores a session's footage (`at`): from its first frame the later steps'
    # click would land on another picture than the one they were written on.
    was = (before.get("playback") or {}).get("seconds")
    back = await stack.http.post(f"{stack.bridge_url}/feed", params={"source": "footage", "footage": before.get("footage"),
                                                                     **({"at": was} if isinstance(was, (int, float)) else {})},
                                 timeout=30)
    feed = (await stack.http.get(f"{stack.bridge_url}/feed")).json()
    live, _ = await stack.wait(lambda h: h["state"] == "live", 15, "live on the sim's own feed")
    out.check("and the feed goes back to what it was", back.status_code == 200 and live
              and feed.get("footage") == before.get("footage") and not (feed.get("playback") or {}).get("paused"),
              f"{back.status_code}: {feed.get('footage')} (was {before.get('footage')})")


SOURCE_SPECIMEN = "Harness: onion skin"  # the user's specimen for the still (asset-meta), as nothing else would name it


async def scenario_actions(stack: Stack, viewer: Viewer) -> Outcome:
    """The Actions panel (docs/design/2026-10-13-actions-panel.md), as the studio sends it: `GET /api/actions` lists the
    agent's tools with their arguments; the `action` command runs a capability's tool by the user's hand: set the
    objective, then measure the selected outline (a size in µm, from the app); "Run with" a model that means nothing is
    refused and nothing runs in its place; Clear outlines clears. Behaviour, not timing (no cloud)."""
    out = Outcome("actions: run by hand through the agent's tools, Run with refused, clear (no cloud)")

    async def action(capability: str, args: dict | None = None, **more) -> dict:
        since = len(viewer.results)
        await viewer.send({"type": "action", "capability": capability, "args": args or {}, **more})
        return await viewer.result_for("action", since, timeout=40) or {}

    listed = (await stack.http.get(f"{stack.orch_url}/api/actions", timeout=30)).json().get("tools", {})
    out.check("the panel's forms: every tool with its arguments", {"measure", "set_objective", "clear"} <= set(listed)
              and listed["measure"]["params"].get("required") == ["object"], str(sorted(listed))[:200])
    oid = await _outline_a_spot(stack, viewer, out)
    if oid is None:
        return out
    await viewer.send({"type": "select", "id": oid})
    objective = await action("objective", {"objective": 40})
    out.check("Set the objective, by hand", objective.get("ok") is True and "40x" in str(objective.get("summary")), str(objective)[:200])
    measured = await action("measure", {"object": oid})
    out.check("How big is it, on the selected outline: a size in µm from the app",
              measured.get("ok") is True and "µm" in str(measured.get("summary")) and measured.get("title") == "How big is it",
              str(measured)[:200])
    refused = await action("clear", use={"outline": "no-such-model"})
    out.check("Run with a model that means nothing: refused, and nothing ran in its place",
              refused.get("ok") is False and oid in viewer.scene, f"{str(refused)[:160]}; scene {list(viewer.scene)}")
    unknown = await action("no-such-action")
    out.check("an action that is not here is refused", unknown.get("ok") is False and "no action" in str(unknown.get("error")), str(unknown)[:160])
    cleared = await action("clear")
    await _until(lambda: oid not in viewer.scene, 5)
    out.check("Clear outlines, by hand", cleared.get("ok") is True and oid not in viewer.scene, f"{str(cleared)[:160]}; scene {list(viewer.scene)}")
    # Outline every cell by hand: said in plain words with its table; the table in µm (the objective is set) and its CSV.
    cells = await action("cells")
    table_id = cells.get("table")
    pins = cells.get("tableIdentity") or {}
    out.check("Outline every cell: what it measured in plain words, and its table",
              cells.get("ok") is True and str(cells.get("said", "")).startswith("Outlined and measured") and bool(table_id) and bool(pins.get("session")) and isinstance(pins.get("revision"), int),
              str(cells)[:200])
    if table_id:
        table = (await stack.http.get(f"{stack.orch_url}/api/resources/{table_id}/table", params=pins, timeout=30)).json()
        units = {c["key"]: c["unit"] for c in table.get("columns", [])}
        out.check("its table: a row per cell, sizes in µm by the capture's own scale",
                  table.get("count", 0) > 0 and len(table.get("rows", [])) == table.get("count") and units.get("areaPx") == "µm²",
                  f"{table.get('count')} rows, units {units}")
        got = await stack.http.get(f"{stack.orch_url}/api/resources/{table_id}/table.csv", params=pins, timeout=30)
        first = got.text.splitlines()[0] if got.text else ""
        out.check("its CSV downloads with each column's unit", got.status_code == 200 and "attachment" in got.headers.get(
            "content-disposition", "") and first.startswith("Cell,Area (µm²)"), f"{got.status_code} {first[:80]}")
    return out


async def scenario_library(stack: Stack, viewer: Viewer) -> Outcome:
    """All assets (docs/design/2026-10-12-workspace-and-assets.md; ADR 0022's amendment), on the sim's own feed only: a
    snapshot's picture is in the library; in another session, previewing it (its details, its sessions, its file) adds
    it to nothing; Add (the `source` command) puts it on stage and makes it that session's; Move to trash takes it out of
    the library and of both sessions' lists, and Undo brings it back to both. Then the sim's own feed is put back and
    the session told it is live. Behaviour, not timing (no cloud)."""
    out = Outcome("library: preview adds nothing, Add associates, the trash and its Undo (no cloud)")
    if getattr(stack, "attach", False):
        out.check("needs its own stack (not --attach)", False)
        return out

    async def get(url: str, **params):
        return await stack.http.get(url, params=params, timeout=30)

    async def command(kind: str, **msg) -> dict:
        since = len(viewer.results)
        await viewer.send({"type": kind, **msg})
        return await viewer.result_for(kind, since, timeout=40) or {}

    async def sources_of(sid: str) -> list[str]:
        return [r["id"] for r in (await get(f"{stack.orch_url}/api/sessions/{sid}/sources")).json().get("sources", [])]

    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    first = viewer.session.get("id")
    before = (await get(f"{stack.bridge_url}/feed")).json()
    second = None
    try:
        began = time.time()
        snap = await command("tool", name="snapshot", args={"overlays": False})
        out.check("a snapshot", snap.get("ok") is True, str(snap)[:200])
        still = None
        end = time.monotonic() + 25
        while still is None and time.monotonic() < end:
            found = [a for a in (await get(f"{stack.orch_url}/api/library", kind="still", fresh=1)).json().get("assets", [])
                     if a.get("from") == "snapshot" and a.get("origin") == first and (a.get("capturedAt") or 0) >= began - 5]
            still = max(found, key=lambda a: a.get("capturedAt") or 0) if found else None
            if still is None:
                await asyncio.sleep(0.5)
        if not out.check("its picture is in the library, made in this session", bool(still), str(still)):
            return out
        aid = still["id"]
        await viewer.send({"type": "session", "action": "new", "name": "Harness: library"})
        await _until(lambda: viewer.session.get("id") not in (None, first), 10)
        second = viewer.session.get("id")
        # Previewed: its details (with the session it was made in), its file; the new session has nothing of it.
        details = (await get(f"{stack.orch_url}/api/library/{aid}")).json()
        file = await get(f"{stack.orch_url}/api/library/{aid}/file")
        out.check("previewing it reads its details, the sessions using it and its file, and adds it to nothing",
                  details.get("id") == aid and first in [s.get("id") for s in details.get("sessions", [])]
                  and file.status_code == 200 and len(file.content) > 0 and aid not in await sources_of(second),
                  f"{details.get('sessions')}, file {file.status_code} ({len(file.content)} bytes), {await sources_of(second)}")
        # Add to this session: on stage, and the session's.
        added = await command("source", open=aid)
        out.check("Add puts it on stage and makes it this session's", added.get("ok") is True and aid in await sources_of(second),
                  f"{added}, {await sources_of(second)}")
        # Move to trash: out of the library and both sessions' lists; Undo brings it back to both.
        trashed = await command("asset-trash", asset=aid)
        listed = [a["id"] for a in (await get(f"{stack.orch_url}/api/library", kind="still", fresh=1)).json().get("assets", [])]
        gone = (await get(f"{stack.orch_url}/api/library/{aid}")).status_code
        out.check("moved to the trash, it leaves the library and every session's list (it was on stage: it says so)",
                  trashed.get("ok") is True and trashed.get("wasOnStage") is True and aid not in listed and gone == 404
                  and aid not in await sources_of(second) and aid not in await sources_of(first),
                  f"{trashed}, listed {aid in listed}, {gone}")
        await command("source", open="live", switch=False)  # as the studio does when it was on stage
        restored = await command("asset-restore", asset=aid)
        out.check("Undo brings it back to the library and to both sessions",
                  restored.get("ok") is True and (await get(f"{stack.orch_url}/api/library/{aid}")).status_code == 200
                  and aid in await sources_of(second) and aid in await sources_of(first), str(restored))
    finally:
        # Left as found: the first session, the sim's own feed and the session told it is live (never `source=camera`).
        if viewer.session.get("id") != first:
            await viewer.send({"type": "session", "action": "switch", "id": first})
            await _until(lambda: viewer.session.get("id") == first, 10)
        put = await stack.http.post(f"{stack.bridge_url}/feed", params={"source": "footage", "footage": before.get("footage")},
                                    timeout=30)
        told = await command("source", open="live", switch=False)
        live, _ = await stack.wait(lambda h: h["state"] == "live", 15, "live on the sim's own feed")
        out.check("left as found: the sim's own feed, the session live", put.status_code == 200 and told.get("ok") is True and live,
                  f"{put.status_code}, {told}")
    return out


async def scenario_sources(stack: Stack, viewer: Viewer) -> Outcome:
    """A session's sources (ADR 0022; docs/design/2026-10-12-session-sources.md), on the sim's own feed only (never
    `source=camera`, which would open a real device): a short Record's camera video joins the session's sources and the
    library; opened, the bridge plays it held and frames keep coming; a snapshot's picture is an asset too, played as a
    still at the bridge's rate, and a click on it outlines; its specimen given by the user is the session's subject and
    what the models read of it, and its objective is the scale; removed, its specimen leaves the session's, and Undo
    brings it back; a new session and back, the first session's sources say what was on stage, and it lands there;
    then the sim's own feed is put back and the session told it is live. Behaviour, not timing (no cloud)."""
    out = Outcome("sources: a Record video, a still analysed, metadata, remove and Undo, sessions toggled (no cloud)")
    if getattr(stack, "attach", False):
        out.check("needs its own stack (not --attach)", False)
        return out

    async def get(url: str, **params) -> dict:
        return (await stack.http.get(url, params=params, timeout=30)).json()

    async def command(kind: str, **msg) -> dict:
        since = len(viewer.results)
        await viewer.send({"type": kind, **msg})
        return await viewer.result_for(kind, since, timeout=40) or {}

    async def listed(sid: str) -> dict:
        return await get(f"{stack.orch_url}/api/sessions/{sid}/sources")

    async def feed() -> dict:
        return await get(f"{stack.bridge_url}/feed")

    async def newest(kind: str, made: str, sid: str, since: float, seconds: float = 25.0) -> dict | None:
        """The newest asset of the library's that a session made (`from`, its `origin`) since a moment, the folders read
        afresh, waited for (a Record's video is listed once its dataset says it has a part)."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            found = [a for a in (await get(f"{stack.orch_url}/api/library", kind=kind, fresh=1)).get("assets", [])
                     if a.get("from") == made and a.get("origin") == sid and (a.get("capturedAt") or 0) >= since - 5]
            if found:
                return max(found, key=lambda a: a.get("capturedAt") or 0)
            await asyncio.sleep(0.5)
        return None

    async def rates(seconds: float = 3.0) -> tuple[int, int]:
        """Frames the bridge took and frames the viewer received over `seconds`: compared between two sources measured
        the same way, so a loaded machine slows both alike."""
        took = (await stack.bridge_health()).get("frameCount") or 0
        t0 = time.time()
        await asyncio.sleep(seconds)
        return max(0, ((await stack.bridge_health()).get("frameCount") or 0) - took), len(viewer.frames_since(t0))

    def specimens(listing: dict, sid: str) -> list:
        return next((r.get("specimens") or [] for r in listing.get("sessions", []) if r.get("id") == sid), [])

    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    first = viewer.session.get("id")
    before = await feed()
    try:
        # A short Record: its camera video is one of this session's sources, and in the library.
        began = time.time()
        if not await _record_briefly(stack, viewer, out):
            return out
        video = await newest("recording", "record", first, began)
        if not out.check("the Record's camera video is in the library, made in this session", bool(video), str(video)):
            return out
        rows = {r["id"]: r for r in (await listed(first)).get("sources", [])}
        out.check("and among the session's sources, joined on its own", rows.get(video["id"], {}).get("joined") is True,
                  str(sorted(rows)))
        # Opened: the server plays it on the bridge, held; frames keep coming (a slide at rest).
        opened = await command("source", open=video["id"], paused=True, at=0)
        played = await feed()
        live, _ = await stack.wait(lambda h: h["state"] == "live", 15, "live on the Record's video")
        held_took, held_frames = await rates()
        out.check("opened, the bridge plays it held and frames keep coming",
                  opened.get("ok") is True and str(played.get("footage") or "").startswith("asset:")
                  and (played.get("playback") or {}).get("paused") is True and live and held_frames >= 10,
                  f"{opened}, {played.get('footage')}, {played.get('playback')}, the bridge took {held_took} frames in 3 s, "
                  f"the viewer got {held_frames}")
        # A still: a snapshot's picture is an asset; opened, the bridge plays it as a still at its rate, and a click
        # on it outlines.
        began = time.time()
        snap = await command("tool", name="snapshot", args={"overlays": False})
        out.check("a snapshot", snap.get("ok") is True, str(snap)[:200])
        still = await newest("still", "snapshot", first, began)
        if not out.check("its picture is in the library, made in this session", bool(still), str(still)):
            return out
        sid_still = still["id"]
        opened = await command("source", open=sid_still)
        played = await feed()
        live, _ = await stack.wait(lambda h: h["state"] == "live", 15, "live on the still")
        still_took, still_frames = await rates()
        out.check("the bridge plays the picture as a still, at the rate it plays a recording, and frames keep coming",
                  opened.get("ok") is True and str(played.get("footage") or "").startswith("asset:")
                  and (played.get("playback") or {}).get("still") is True and live
                  and still_took >= held_took // 2 and still_frames >= 10,
                  f"{played.get('footage')}, {played.get('playback')}, the bridge took {still_took} frames in 3 s "
                  f"(the held recording: {held_took}), the viewer got {still_frames} ({held_frames})")
        oid = await _outline_a_spot(stack, viewer, out)
        out.check("the click on the still is outlined on the view", bool(oid) and await _until(lambda: oid in viewer.scene, 5),
                  f"{oid}, scene {list(viewer.scene)}")
        # Its specimen, given by the user: the session's subject, and what the models read of the stage.
        meta = await command("asset-meta", asset=sid_still, set={"specimen": SOURCE_SPECIMEN})
        named = await _until(lambda: viewer.session.get("specimen") == SOURCE_SPECIMEN, 10)
        out.check("its specimen, given by the user, is the session's subject", meta.get("ok") is True and named,
                  f"{meta}, the session's specimen {viewer.session.get('specimen')!r}")
        entries = (await get(f"{stack.orch_url}/api/resources", kind="asset")).get("entries", [])
        line = next((e.get("summary") or "" for e in entries if e.get("id") == sid_still), "")
        out.check("and its line of metadata (the Board's stage line) names it, said to be the user's",
                  f"specimen '{SOURCE_SPECIMEN}' (named by the user)" in line, line)
        meta = await command("asset-meta", asset=sid_still, set={"objective": 40})
        scaled = await _until(lambda: viewer.scale.get("asset") == sid_still and viewer.scale.get("objective") == 40
                              and (viewer.scale.get("umPerPx") or 0) > 0, 10)
        out.check("its objective is the scale while it is on stage", meta.get("ok") is True and scaled, str(viewer.scale))
        # Removed: its specimen leaves the session's (the sessions list's row); Undo brings it back.
        sessions = await get(f"{stack.orch_url}/api/sessions")
        was = SOURCE_SPECIMEN in specimens(sessions, first)
        removed = await command("source-remove", asset=sid_still)
        sessions = await get(f"{stack.orch_url}/api/sessions")
        after = await listed(first)
        out.check("removed, it leaves the session's sources and its specimen leaves the session's",
                  was and removed.get("ok") is True and removed.get("wasOnStage") is True
                  and SOURCE_SPECIMEN not in specimens(sessions, first) and sid_still in after.get("removed", [])
                  and sid_still not in [r["id"] for r in after.get("sources", [])],
                  f"before {was}, {removed}, specimens {specimens(sessions, first)}, removed {after.get('removed')}")
        if removed.get("next"):  # the studio puts the next source on stage, as its × does
            await command("source", open=removed["next"], paused=True)
        restored = await command("source-restore", asset=sid_still)
        sessions = await get(f"{stack.orch_url}/api/sessions")
        after = await listed(first)
        out.check("Undo brings it and its specimen back",
                  restored.get("ok") is True and SOURCE_SPECIMEN in specimens(sessions, first)
                  and sid_still in [r["id"] for r in after.get("sources", [])] and sid_still not in after.get("removed", []),
                  f"{restored}, specimens {specimens(sessions, first)}")
        await command("source", open=sid_still)
        # A new session, then back: the first's sources say what was on stage, and the studio lands it there.
        await viewer.send({"type": "session", "action": "new", "name": "Harness: sources B"})
        await _until(lambda: viewer.session.get("id") not in (None, first), 10)
        second = viewer.session.get("id")
        theirs, ours = await listed(first), await listed(second)
        out.check("in a new session, the first session's sources say the still was on stage; the new one's, nothing",
                  second != first and theirs.get("onStage") == sid_still and ours.get("onStage") is None
                  and not ours.get("sources"), f"{second}: {theirs.get('onStage')} / {ours.get('onStage')}, "
                  f"{[r['id'] for r in ours.get('sources', [])]}")
        await viewer.send({"type": "session", "action": "switch", "id": first})
        back = await _until(lambda: viewer.session.get("id") == first, 10)
        theirs = await listed(first)
        landed = await command("source", open=theirs.get("onStage") or "live", paused=True)
        played = await feed()
        on_stage = await _until(lambda: viewer.scale.get("asset") == sid_still, 10)
        out.check("back in the first session, it lands on the still (its scale again)",
                  back and theirs.get("onStage") == sid_still and landed.get("ok") is True
                  and (played.get("playback") or {}).get("still") is True and on_stage,
                  f"{theirs.get('onStage')}, {landed}, {played.get('playback')}, {viewer.scale}")
    finally:
        # Left as found: the sim's own feed where it was, and the session told it is live (its test footage stands for
        # the live camera; never `source=camera`).
        if viewer.session.get("id") != first:
            await viewer.send({"type": "session", "action": "switch", "id": first})
            await _until(lambda: viewer.session.get("id") == first, 10)
        was = (before.get("playback") or {}).get("seconds")
        put = await stack.http.post(f"{stack.bridge_url}/feed", params={"source": "footage", "footage": before.get("footage"),
                                                                         **({"at": was} if isinstance(was, (int, float)) else {})},
                                    timeout=30)
        told = await command("source", open="live", switch=False)
        now = await feed()
        live, _ = await stack.wait(lambda h: h["state"] == "live", 15, "live on the sim's own feed")
        out.check("left as found: the sim's own feed, the session live and no asset's scale",
                  put.status_code == 200 and told.get("ok") is True and live and now.get("footage") == before.get("footage")
                  and not (now.get("playback") or {}).get("paused") and (await listed(first)).get("onStage") == "live"
                  and await _until(lambda: not viewer.scale.get("asset"), 5),
                  f"{put.status_code}, {told}, {now.get('footage')} (was {before.get('footage')}), {viewer.scale}")
    return out


def _interior_point(polygon: list) -> tuple[float, float]:
    """A point well inside a member's outline (its mask's farthest point from the edge), in the outline's own pixels."""
    pts = np.asarray(polygon, np.float32)
    x0, y0 = np.floor(pts.min(axis=0)) - 2
    x1, y1 = np.ceil(pts.max(axis=0)) + 2
    mask = np.zeros((int(y1 - y0) + 1, int(x1 - x0) + 1), np.uint8)
    cv2.fillPoly(mask, [np.round(pts - [x0, y0]).astype(np.int32)], 255)
    depth = cv2.distanceTransform(mask, cv2.DIST_L2, 3)
    y, x = np.unravel_index(int(np.argmax(depth)), depth.shape)
    return float(x + x0), float(y + y0)


async def scenario_still_population(stack: Stack, viewer: Viewer) -> Outcome:
    """S5a through the real bridge (FFmpeg's scale and letterbox, MJPEG, the motion tracker on a looping still): a This
    Tab run of the edition's cell finder on a portrait still, pillarboxed in the bridge's frames, is drawn on the
    specimen. The layer wire's `fit` is the bridge's own (still_fit.fit for the frame size), every member is verified, a
    click at a member's fitted place selects it and one in the letterbox does not, the run places no masks window and
    leaves nothing pending (kept in Data, listed with why; `action-placed` says so), and the live feed again hides the
    layer. Behaviour, not timing (no cloud). The studio's drawing and hover through the fit are a browser's (vitest
    covers their math; their frame cost is measured in a browser, not here)."""
    from orchestrator import still_fit
    from orchestrator.workspace_views import SPECIMEN_REASON

    out = Outcome("still population: a run on a portrait still is drawn on it through the bridge's letterbox, no window (no cloud)")
    if getattr(stack, "attach", False):
        out.check("needs its own stack (not --attach)", False)
        return out

    async def get(url: str, **params) -> dict:
        return (await stack.http.get(url, params=params, timeout=30)).json()

    async def command(kind: str, timeout: float = 40, **msg) -> dict:
        since = len(viewer.results)
        await viewer.send({"type": kind, **msg})
        return await viewer.result_for(kind, since, timeout=timeout) or {}

    async def asset(file: str, seconds: float = 25.0) -> dict | None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            found = [a for a in (await get(f"{stack.orch_url}/api/library", kind="still", fresh=1)).get("assets", [])
                     if a.get("file") == file]
            if found:
                return found[0]
            await asyncio.sleep(0.5)
        return None

    def layer() -> dict:
        return viewer.layers[-1][1] if viewer.layers else {}

    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    sid = viewer.session.get("id")
    before = await get(f"{stack.bridge_url}/feed")
    out.check("footage moved to a still view", await stack.seek(CELLS_MOVE_AT))
    await stack.http.post(f"{stack.bridge_url}/feed/pause", params={"paused": 1})
    await asyncio.sleep(1.5)
    try:
        # A portrait picture of the view (a snapshot's, cropped to 3:4): the bridge pillarboxes it in its 16:9 frames.
        began = time.time()
        snap = await command("tool", name="snapshot", args={"overlays": False})
        metas = []
        for _ in range(50):
            metas = [m for m in Path(stack.paths.snapshots).glob("*/snapshot-*.json") if m.stat().st_mtime >= began - 5
                     and m.with_suffix(".png").is_file()]
            if metas:
                break
            await asyncio.sleep(0.5)
        if not out.check("a snapshot of the view, on disk", snap.get("ok") is True and bool(metas), str(snap)[:160]):
            return out
        meta = max(metas, key=lambda m: m.stat().st_mtime)
        picture = cv2.imread(str(meta.with_suffix(".png")), cv2.IMREAD_COLOR)
        h, w = picture.shape[:2]
        width = (h * 3 // 4) & ~1
        portrait = meta.with_name(f"{meta.stem}-portrait.png")
        cv2.imwrite(str(portrait), picture[:, (w - width) // 2:(w - width) // 2 + width])
        facts = json.loads(meta.read_text())
        portrait.with_suffix(".json").write_text(json.dumps({**facts, "wallAt": time.time(), "overlays": False}))
        found = await asset(portrait.name)
        if not out.check("the portrait picture is a still in the library", bool(found), portrait.name):
            return out
        opened = await command("source", open=found["id"])
        live, _ = await stack.wait(lambda h: h["state"] == "live" and h["motion"] and h["motion"]["tracked"], 20,
                                   "live on the portrait still")
        out.check("it plays on the Stage", opened.get("ok") is True and live, str(opened)[:160])
        # A This Tab run, as the studio sends it.
        run = f"sim-still-{int(time.time() * 1000)}"
        origin = {"session": sid, "tab": "main", "placementId": f"pending-{run}", "run": run, "input": {"source": found["id"]}}
        reply = await command("action", timeout=180, capability="cells", args={}, run=run, workspaceOrigin=origin)
        pop = viewer.population
        count = int(pop.get("count") or 0)
        if not out.check("the run found members on the still, kept, its result on the specimen once checked there",
                         reply.get("ok") is True and count > 0 and reply.get("placed") == "specimen",
                         f"{str(reply)[:200]}; {count} members"):
            return out
        verified = await _until(lambda: layer().get("population") == pop.get("id") and layer().get("fit") is not None
                                and len(layer().get("verified") or []) == count, 15)
        _, header, _ = viewer.latest()
        fit = still_fit.fit(still_fit.read_layout(portrait), int(header["w"]), int(header["h"]))
        out.check("every member is verified on it, placed by the bridge's own letterbox",
                  verified and fit is not None and layer().get("fit") == fit.wire() and fit.rect[0] > 0,
                  f"{len(layer().get('verified') or [])} of {count}, fit {layer().get('fit')} (expected "
                  f"{fit.wire() if fit else None}); why {layer().get('why')!r}")
        # A click at a member's fitted place selects it (member_hit, no model); one in the letterbox selects none.
        members = pop.get("members") or []
        big = max(members, key=lambda m: (m.get("measure") or {}).get("areaPx") or 0)
        a, b, c, d, e, f = layer()["fit"]["matrix"]
        x, y = _interior_point(max(big["polygons"], key=len))
        _, header, _ = viewer.latest()
        hit = await command("click", frameId=header["id"], x=a * x + b * y + c, y=d * x + e * y + f, identify=False)
        out.check("a click at a member's place on the Stage selects that member", hit.get("member") == big["id"],
                  f"{hit}; expected {big['id']}")
        rx, ry, rw, rh = fit.rect
        missed = await command("click", frameId=header["id"], x=rx / 2, y=ry + rh / 2, identify=False)
        out.check("a click in the letterbox selects no member", "member" not in missed, str(missed)[:160])
        # No masks window and nothing pending: kept in Data, listed with why; every studio was told.
        settled = {}

        async def kept() -> bool:
            nonlocal settled
            settled = await command("workspace-layout", session=sid, schema=5)
            return any(row.get("origin", {}).get("run") == run and row.get("reason") == SPECIMEN_REASON
                       for row in settled.get("completions") or [])
        end = time.monotonic() + 15
        while not await kept() and time.monotonic() < end:
            await asyncio.sleep(1.0)
        items = [item for view in (settled.get("layout") or {}).get("views", []) for item in view.get("items", [])]
        out.check("no masks window, nothing pending: kept in Data, listed as drawn on the specimen",
                  await kept() and not any(item.get("resource", {}).get("id") == pop.get("id") for item in items)
                  and run not in [p.get("run") for p in settled.get("pending") or []]
                  and {"type": "action-placed", "run": run, "placed": "specimen"} in viewer.placements,
                  f"completions {[(r.get('origin', {}).get('run'), r.get('state'), r.get('reason')) for r in settled.get('completions') or []]}, "
                  f"pending {[p.get('run') for p in settled.get('pending') or []]}, placements {viewer.placements[-3:]}")
        # The live feed again (the sim's own footage stands for the camera): the layer is hidden, nothing drawn on it.
        was = (before.get("playback") or {}).get("seconds")
        await stack.http.post(f"{stack.bridge_url}/feed", params={"source": "footage", "footage": before.get("footage"),
                                                                   **({"at": was} if isinstance(was, (int, float)) else {})},
                              timeout=30)
        told = await command("source", open="live", switch=False)
        hidden = await _until(lambda: layer().get("population") is None, 10)
        out.check("the live feed on the Stage hides the layer", told.get("ok") is True and hidden, f"{told}, {layer().get('population')}")
    finally:
        await stack.http.post(f"{stack.bridge_url}/feed/pause", params={"paused": 0})
        was = (before.get("playback") or {}).get("seconds")
        await stack.http.post(f"{stack.bridge_url}/feed", params={"source": "footage", "footage": before.get("footage"),
                                                                   **({"at": was} if isinstance(was, (int, float)) else {})},
                              timeout=30)
        await command("source", open="live", switch=False)
        await stack.wait(lambda h: h["state"] == "live", 15, "live on the sim's own feed")
    return out


async def scenario_no_camera(stack: Stack, viewer: Viewer) -> Outcome:
    """No camera (Will, 2026-10-15): chosen, the bridge captures, scans and resets nothing for 30 s and the app says no
    camera is in use (never a fault); a picture opened plays and a click on it outlines; a camera chosen again goes
    live. On the sim's own feed: its footage stands for the camera, and `source=camera` is asked only while No camera is
    chosen, when it opens nothing. Behaviour, not timing (no cloud)."""
    out = Outcome("no camera: nothing scanned or reset for 30 s, said calmly, a picture plays, a camera goes live again (no cloud)")
    if getattr(stack, "attach", False):
        out.check("needs its own stack (not --attach)", False)
        return out
    header = {"X-MicroscopeMind": "camera-source"}

    async def get(url: str, **params) -> dict:
        return (await stack.http.get(url, params=params, timeout=30)).json()

    async def command(kind: str, **msg) -> dict:
        since = len(viewer.results)
        await viewer.send({"type": kind, **msg})
        return await viewer.result_for(kind, since, timeout=40) or {}

    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    before = await get(f"{stack.bridge_url}/feed")
    was_kind = (await get(f"{stack.bridge_url}/api/health")).get("sourceKind") or "continuity"
    # A picture to open later: a snapshot of the sim's view, taken while it plays.
    sid, began = viewer.session.get("id"), time.time()
    snap = await command("tool", name="snapshot", args={"overlays": False})
    still = None
    for _ in range(50):
        found = [a for a in (await get(f"{stack.orch_url}/api/library", kind="still", fresh=1)).get("assets", [])
                 if a.get("from") == "snapshot" and a.get("origin") == sid and (a.get("capturedAt") or 0) >= began - 5]
        if found:
            still = max(found, key=lambda a: a.get("capturedAt") or 0)
            break
        await asyncio.sleep(0.5)
    if not out.check("a snapshot to open later, in the library", snap.get("ok") is True and bool(still), f"{str(snap)[:120]}, {still}"):
        return out
    try:
        chosen = await stack.http.post(f"{stack.bridge_url}/api/source", params={"kind": "none"}, headers=header, timeout=30)
        off = await stack.http.post(f"{stack.bridge_url}/feed", params={"source": "camera"}, timeout=30)
        then = await get(f"{stack.bridge_url}/api/health")
        await asyncio.sleep(30)
        now = await get(f"{stack.bridge_url}/api/health")
        counts = ("scanCount", "continuityResets", "restartCount")
        out.check("No camera: for 30 s nothing is captured, scanned or reset",
                  chosen.status_code == 200 and off.status_code == 200 and now.get("state") == "no_camera"
                  and not now.get("running") and all(now.get(k) == then.get(k) for k in counts) and now.get("diagnosis") is None,
                  f"{now.get('state')}, running {now.get('running')}, "
                  + ", ".join(f"{k} {then.get(k)}→{now.get(k)}" for k in counts) + f", diagnosis {now.get('diagnosis')}")
        calm, _ = await stack.wait(lambda h: h["state"] == "no_camera", 10, "no_camera")
        doctor = await get(f"{stack.bridge_url}/api/doctor")
        out.check("the app says no camera is in use, never a fault", calm and doctor.get("mode") == "off"
                  and doctor.get("headline") == "No camera", f"{calm}, {doctor.get('mode')}, {doctor.get('headline')}")
        opened = await command("source", open=still["id"])
        live, _ = await stack.wait(lambda h: h["state"] == "live", 15, "live on the picture")
        oid = await _outline_a_spot(stack, viewer, out)
        out.check("a picture opened plays, and a click on it outlines", opened.get("ok") is True and live and bool(oid)
                  and await _until(lambda: oid in viewer.scene, 5), f"{opened}, live {live}, {oid}")
    finally:
        # A camera chosen again goes live: the sim's own feed (it stands for the camera), then the source it had.
        was = (before.get("playback") or {}).get("seconds")
        put = await stack.http.post(f"{stack.bridge_url}/feed", params={"source": "footage", "footage": before.get("footage"),
                                                                         **({"at": was} if isinstance(was, (int, float)) else {})},
                                    timeout=30)
        back = await stack.http.post(f"{stack.bridge_url}/api/source", params={"kind": was_kind}, headers=header, timeout=30)
        told = await command("source", open="live", switch=False)
        live, _ = await stack.wait(lambda h: h["state"] == "live", 15, "live again")
        health = await get(f"{stack.bridge_url}/api/health")
        out.check("a camera chosen again goes live", put.status_code == 200 and back.status_code == 200 and told.get("ok") is True
                  and live and health.get("sourceKind") == was_kind, f"{put.status_code}, {back.status_code}, {told}, {health.get('sourceKind')}")
    return out


def _ffprobe_frames(path: Path) -> int:
    result = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                             "stream=nb_read_packets", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return int(result.stdout.strip().splitlines()[0])
    except (IndexError, ValueError):
        return 0


def _jsonl(path: Path) -> list[dict]:
    out = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


async def scenario_dataset(stack: Stack, viewer: Viewer) -> Outcome:
    """Record's dataset (orchestrator/datasets/, docs/datasets.md): started, a click, the objective named
    and a snapshot taken, then the app restarted and the camera bridge restarted, then stopped. The
    dataset holds a playable video for each run of the bridge with a sidecar line per frame (the bridge
    marks them as test footage, the app never does), the app's frame facts joined to them, the records,
    the pictures and the gap; the Data light turns blue once it has seen every file grow, again after
    each restart, and says "saved" once the stopped dataset is checked whole; the live view stays as
    fast (no cloud)."""
    out = Outcome("a dataset collected across restarts (no cloud)")
    if getattr(stack, "attach", False):
        out.check("needs its own stack (not --attach)", False)
        return out

    async def until(test, seconds=10.0):
        end = time.time() + seconds
        while time.time() < end and not test():
            await asyncio.sleep(0.1)
        return test()

    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    since = len(viewer.results)
    await viewer.send({"type": "dataset", "on": True})
    reply = await viewer.result_for("dataset", since)
    if not out.check("Record's dataset starts", bool(reply and reply.get("ok") and reply["dataset"]["on"]), str(reply)):
        return out

    def light(v: Viewer) -> str:
        return (v.dataset.get("check") or {}).get("state", "")
    out.check("the Data light turns blue once it has seen every file of the dataset grow",
              await until(lambda: light(viewer) == "ok", 20), str(viewer.dataset.get("check")))
    folder = stack.paths.datasets / reply["dataset"]["id"]
    part1 = folder / "video" / "part-1.mp4"
    out.check("the camera's video is kept in it as it records",
              await until(lambda: part1.exists() and part1.stat().st_size > 0, 15),
              f"{part1.stat().st_size if part1.exists() else 0} bytes")

    t0 = time.time()
    from .outline_fixture import held_outlines

    # Recording remains moving footage; hold the labelled view only for the click itself.
    async with held_outlines(stack, viewer) as spots:
        target = spots[0]
        _, header, _ = viewer.latest()
        since = len(viewer.results)
        await viewer.send({"type": "click", "frameId": header["id"], "x": target["x"], "y": target["y"]})
        clicked = await viewer.result_for("click", since)
        out.check("a click while it collects", bool(clicked and clicked.get("ok")), str(clicked))
    await viewer.send({"type": "scale", "objective": 40})
    since = len(viewer.results)
    await viewer.send({"type": "tool", "name": "snapshot", "args": {}})
    snap = await viewer.result_for("tool", since, timeout=20)
    out.check("a snapshot while it collects", bool(snap and snap.get("ok")), str(snap)[:200])
    # The click's speed while it collects (frame facts written for every frame, off the frame path).
    click_ms = [e.get("ms") for e in _logged_since(t0, ("click",)) if isinstance(e.get("ms"), (int, float))]

    await stack.restart_orchestrator()
    again = Viewer(stack.ws_url)
    task = asyncio.create_task(again.run())
    try:
        resumed = await until(lambda: again.dataset.get("on") and again.dataset.get("id") == folder.name, 20)
        out.check("after the app restarts the same dataset carries on, its gap noted",
                  resumed and (again.dataset.get("resumedAfter") or 0) > 0, str(again.dataset))
        await stack.restart_bridge()
        part2 = folder / "video" / "part-2.mp4"
        out.check("after the camera bridge restarts its new recording is a new part",
                  await until(lambda: part2.exists() and part2.stat().st_size > 0, 30), str(sorted(p.name for p in (folder / "video").glob("*"))))
        out.check("after both restarts the Data light is blue again", await until(lambda: light(again) == "ok", 30),
                  str(again.dataset.get("check")))
        await asyncio.sleep(2.0)
        since = len(again.results)
        await again.send({"type": "dataset", "on": False})
        off = await again.result_for("dataset", since)
        out.check("Record's Stop ends it", bool(off and off.get("ok") and not off["dataset"]["on"]), str(off))
        # The bridge finishes the last file on its own; its sidecar's trailer says it is complete.
        await until(lambda: any(line.get("type") == "saved" for line in _jsonl(folder / "video" / "part-2.frames.jsonl")), 30)
        saved = await until(lambda: light(again) == "saved" or "endCheck" in json.loads((folder / "dataset.json").read_text()), 60)
        end = json.loads((folder / "dataset.json").read_text()).get("endCheck") or {}
        out.check("after the stop the Data light checks it was saved whole, and says so",
                  saved and end.get("ok") is True, str(end or again.dataset.get("check")))
    finally:
        task.cancel()

    summary = json.loads((folder / "dataset.json").read_text())
    out.check("it is complete, with two runs of the app and the gap between them",
              summary["status"] == "complete" and summary["endedBy"] == "user" and len(summary["runs"]) == 2
              and len(summary["gaps"]) == 1, f"{summary['status']}, runs {len(summary['runs'])}, gaps {len(summary['gaps'])}")
    joined = total = 0
    sidecar_frames: set[tuple] = set()
    spans: list[tuple] = []  # (boot, first seq, last seq) of each part
    for part in summary["video"]["parts"]:
        video, sidecar = folder / part["video"], _jsonl(folder / part["sidecar"])
        frames = [line for line in sidecar if "i" in line]
        boot = next((line.get("bootId") for line in sidecar if line.get("type") == "header"), None)
        sidecar_frames |= {(boot, line["seq"]) for line in frames}
        if frames:
            spans.append((boot, frames[0]["seq"], frames[-1]["seq"]))
        in_file = _ffprobe_frames(video)
        out.check(f"part {part['n']} plays, and its sidecar has a line for every frame in it",
                  in_file > 0 and len(frames) >= in_file and len(frames) - in_file < 90,
                  f"{in_file} frames in the file, {len(frames)} sidecar lines")
        out.check(f"part {part['n']}: the bridge marks its frames as test footage",
                  bool(frames) and all(line.get("src") == "footage" for line in frames),
                  str(sorted({line.get("src") for line in frames})))
    # Every frame the app measured while the video was recording is in the video, found by the bridge's
    # frame number: inside a part's first and last frame numbers, none may be missing (the frames before
    # the recorder starts, and after the bridge restarts, are outside every part).
    facts = _jsonl(folder / "frames.jsonl")
    for line in facts:
        seq = line.get("seq")
        if seq is not None and any(line.get("boot") == boot and first <= seq <= last for boot, first, last in spans):
            total += 1
            joined += (line.get("boot"), line.get("seq")) in sidecar_frames
    out.check("the app's frame facts join the video by the bridge's frame numbers",
              total > 100 and joined == total, f"{joined} of {total} measured while it recorded, {len(facts)} in all")
    records = [e["record"] for e in _jsonl(folder / "events.jsonl")]
    have = {"click": any(r.get("kind") == "click" for r in records),
            "objective": any(r.get("type") == "preference" and r.get("key") == "scale" for r in records),
            "gap": any(r.get("event") == "resumed" for r in records),
            "bridge restart": any(r.get("event") == "bridge-restarted" for r in records)}
    out.check("its records hold the click, the objective, the restart and the gap", all(have.values()), str(have))
    pictures = folder / "pictures"
    out.check("its pictures hold the snapshot and the minute before the start",
              any((pictures / "snapshots").glob("*.png")) and any((pictures / "preroll").glob("*.jpg")),
              str(sorted(p.name for p in pictures.iterdir())) if pictures.exists() else "no pictures")
    out.check("clicks stay fast while it collects (p95 under 300 ms)",
              bool(click_ms) and float(np.percentile(click_ms, 95)) < 300, f"{click_ms}")
    return out


def standard_layout_fixture(scenario):
    """Scripted lessons exercise the production legacy lesson path without cloud calls.

    Independent producer/model journeys have their own bounded qualification. Restore the
    prior setting even on failure, including when this harness attaches to an existing app.
    """
    @wraps(scenario)
    async def run(stack, viewer):
        async def settings(changes):
            mark = len(viewer.results)
            await viewer.send({"type": "settings", "settings": changes})
            reply = await viewer.result_for("settings", mark)
            if not reply or not reply.get("ok"):
                raise RuntimeError(f"could not configure scripted lesson: {reply}")
            return reply["settings"]
        previous = (await settings({}))["presentation"]
        try:
            await settings({"presentation": "off"})
            return await scenario(stack, viewer)
        finally:
            await settings({"presentation": previous})
    return run


@standard_layout_fixture
async def scenario_lesson_walkthrough(stack: Stack, viewer: Viewer) -> Outcome:
    """A researcher's quiz is gone through one step at a time, as the live agent does on
    "walk me through it" / "next question" (live session 2026-10-04, 15:10: the voice could not
    run a quiz that existed). Scripted lesson, no cloud."""
    out = Outcome("going through a researcher's lesson step by step (scripted researcher, no cloud)")
    since = len(viewer.results)
    questions = [{"question": f"Question {i}: what do you see at {where}?", "answer": f"answer {i}", "hint": f"hint {i}"}
                 for i, where in ((1, "the edges"), (2, "the centre"), (3, "the dark spots"))]
    questions[1].update(kind="find", target="the centre cell")  # a find step: answered by a click on the slide (quiz.py)
    await viewer.send({"type": "tool", "brief": "a short observation quiz (scripted)", "effort": "standard", "deliverable": "lesson",
                       "fake": {"seconds": 1, "lesson": {"title": "Cell detective", "key_ideas": ["look first"], "questions": questions}}})
    reply = await viewer.result_for("tool", since)
    rid = (reply or {}).get("rid")
    end = time.time() + 30
    while time.time() < end and viewer.research.get(rid, {}).get("status") != "done":
        await asyncio.sleep(0.1)
    session = viewer.research.get(rid, {})
    lesson = next((o for o in session.get("outputs") or [] if o.get("kind") == "html"), None)
    steps = ((lesson or {}).get("content") or {}).get("steps") or []
    if not out.check("the lesson was made with its steps", session.get("status") == "done" and len(steps) == 3,
                     f"{session.get('status')}, {len(steps)} steps"):
        return out
    page = await stack.http.get(stack.orch_url + lesson["url"])
    out.check("each step has an anchor the viewer can scroll to", all(f'id="q{i}"' in page.text for i in (1, 2, 3)),
              f"{page.status_code}, {len(page.text)} bytes")

    async def tool(name, args):
        mark = len(viewer.results)
        await viewer.send({"type": "tool", "name": name, "args": args})
        return await viewer.result_for("tool", mark) or {}

    said = []
    for i in (1, 2, 3):
        r = await tool("result_step", {"researcher": rid, "to": "next"})
        said.append(r.get("summary") or r.get("error") or "")
        await asyncio.sleep(0.2)
        out.check(f"next goes to step {i}, never repeating", r.get("ok") and f"step {i} of 3" in said[-1]
                  and f"Question {i}:" in said[-1] and viewer.presenter.get("step") == f"q{i}", said[-1][:90])
    r = await tool("result_step", {"researcher": rid, "to": "next"})
    out.check("after the last step it says so", r.get("ok") is False and "last" in (r.get("error") or ""), str(r.get("error")))
    r = await tool("read_result", {"researcher": "the quiz"})
    out.check("the agent can read the whole quiz, answers included, as untrusted text",
              r.get("ok") and "untrusted" in (r.get("summary") or "") and "answer 2" in (r.get("summary") or ""), (r.get("summary") or "")[:90])
    out.check("the lesson is open beside the view on the last step", viewer.presenter.get("open") and viewer.presenter.get("rid") == rid
              and viewer.presenter.get("step") == "q3", str(viewer.presenter))
    out.check("step 2 came as a find step, answered on the slide", "This is a find step" in said[1]
              and steps[1].get("kind") == "find", said[1][-160:])
    # How the user did (the model's verdicts, here given directly), and going over what was missed.
    for step, outcome in (("q1", "right"), ("q2", "wrong"), ("q3", "skipped")):
        r = await tool("mark_step", {"researcher": rid, "step": step, "outcome": outcome})
        out.check(f"{step} marked {outcome}", r.get("ok"), str(r.get("summary") or r.get("error"))[:90])
    await asyncio.sleep(0.2)
    out.check("the studio is told how the quiz stands",
              viewer.presenter.get("outcomes") == {"q1": "right", "q2": "wrong", "q3": "skipped"}, str(viewer.presenter))
    r = await tool("result_step", {"researcher": rid, "to": "missed"})
    await asyncio.sleep(0.2)
    out.check("going over what was missed opens the first one missed, a find step",
              r.get("ok") and viewer.presenter.get("step") == "q2" and viewer.presenter.get("stepKind") == "find",
              f"{(r.get('summary') or r.get('error') or '')[:90]} {viewer.presenter}")
    r = await tool("result_step", {"researcher": rid, "to": "missed"})
    out.check("and then the next one missed", r.get("ok") and "step 3 of 3" in (r.get("summary") or ""), str(r.get("summary"))[:90])
    return out


@standard_layout_fixture
async def scenario_revise_lesson(stack: Stack, viewer: Viewer) -> Outcome:
    """A researcher's quiz is changed as asked ("make question 2 easier"): revise_work makes
    version 2 (only question 2 changes), it can be opened beside the view, going through the quiz uses
    version 2, and version 1 stays listed and served (after the first live session, 2026-10-04:
    a "continue" rewrote everything and the old lesson still opened first). Scripted, no cloud."""
    out = Outcome("revising a researcher's lesson into version 2 (scripted researcher, no cloud)")
    since = len(viewer.results)
    questions = [{"question": f"Question {i}: what do you see at {where}?", "answer": f"answer {i}"}
                 for i, where in ((1, "the edges"), (2, "the centre"), (3, "the dark spots"))]
    await viewer.send({"type": "tool", "brief": "a short quiz to revise (scripted)", "effort": "standard", "deliverable": "lesson",
                       "fake": {"seconds": 1, "lesson": {"title": "Cell detective", "key_ideas": ["look first"], "questions": questions},
                                "revise": {"seconds": 1}}})
    rid = ((await viewer.result_for("tool", since)) or {}).get("rid")
    end = time.time() + 30
    while time.time() < end and viewer.research.get(rid, {}).get("status") != "done":
        await asyncio.sleep(0.1)
    if not out.check("the lesson was made", viewer.research.get(rid, {}).get("status") == "done", str(viewer.research.get(rid, {}).get("status"))):
        return out

    async def tool(name, args):
        mark = len(viewer.results)
        await viewer.send({"type": "tool", "name": name, "args": args})
        return await viewer.result_for("tool", mark) or {}

    notices_before = viewer.notice_count
    presenter_before = dict(viewer.presenter)
    r = await tool("revise_work", {"researcher": rid, "target": "quiz", "change": "make question 2 easier"})
    out.check("revise_work starts version 2 at once", r.get("ok") and "version 2" in (r.get("summary") or ""), str(r.get("summary") or r.get("error")))
    saw_revising = False
    end = time.time() + 40
    while time.time() < end:
        session = viewer.research.get(rid, {})
        saw_revising |= session.get("status") == "revising"
        if session.get("status") == "done" and len([o for o in session.get("outputs") or [] if o.get("kind") == "html"]) == 2:
            break
        await asyncio.sleep(0.1)
    session = viewer.research.get(rid, {})
    lessons = [o for o in session.get("outputs") or [] if o.get("kind") == "html"]
    out.check("it showed as revising, then done with two versions", saw_revising and session.get("status") == "done" and len(lessons) == 2,
              f"revising seen: {saw_revising}, {session.get('status')}, {len(lessons)} lessons")
    if len(lessons) != 2:
        return out
    v1, v2 = lessons
    out.check("version 2 replaces version 1, which is kept", v2.get("replaces") == v1.get("id") and v2.get("version") == 2
              and v1.get("current") is False and v2.get("current") is True, f"{v1.get('id')}->{v2.get('replaces')}, v{v2.get('version')}")
    steps1, steps2 = (v1.get("content") or {}).get("steps") or [], (v2.get("content") or {}).get("steps") or []
    out.check("only question 2 changed", len(steps2) == 3 and steps2[0] == steps1[0] and steps2[2] == steps1[2]
              and steps2[1]["prompt"] != steps1[1]["prompt"] and "easier" in steps2[1]["prompt"], (steps2[1]["prompt"] if len(steps2) > 1 else "")[:90])
    await asyncio.sleep(0.3)
    # This wire-only harness has no accepted browser observation/ownership. Background
    # completion must retain the result, never broadcast an unsolicited viewer change.
    # Real requesting-browser auto-open is covered by the accepted-owner regressions.
    out.check("unowned background completion did not redirect the viewer",
              viewer.presenter == presenter_before, str(viewer.presenter))
    opened = await tool("show_result", {"researcher": rid})
    await asyncio.sleep(0.2)
    out.check("version 2 opened when explicitly requested", opened.get("ok") and viewer.presenter.get("open") and viewer.presenter.get("rid") == rid
              and viewer.presenter.get("index") == 0, str(viewer.presenter))
    original = (session.get("history") or [{}])[-1]
    told = viewer.research_activity(rid, original, since=notices_before)
    work_keys = ("id", "kind", "path", "version", "replaces", "content")
    expected_work = [{key: work.get(key) for key in work_keys} for work in lessons]
    out.check("the new version was announced as original application activity",
              len(told) == 1 and told[0]["facts"].get("result") is None
              and told[0]["facts"].get("last") == original
              and [{key: work.get(key) for key in work_keys} for work in told[0]["facts"].get("outputs") or []]
              == expected_work,
              f"{len(told)} received original completions; run {original.get('runId')}; work {v2.get('id')}, v2")
    r = await tool("result_step", {"researcher": rid, "to": "q2"})
    out.check("going through the quiz uses version 2", r.get("ok") and "easier" in (r.get("summary") or ""), (r.get("summary") or "")[:120])
    r = await tool("read_result", {"researcher": rid})
    out.check("reading it lists version 1 as an older version", r.get("ok") and "older version" in (r.get("summary") or ""),
              (r.get("summary") or "")[-120:])
    old_page, new_page = [await stack.http.get(stack.orch_url + o["url"]) for o in (v1, v2)]
    out.check("both pages are served, each with its own question 2", old_page.status_code == new_page.status_code == 200
              and "easier" in new_page.text and "easier" not in old_page.text, f"{old_page.status_code} {new_page.status_code}")
    return out


async def scenario_research_fake(stack: Stack, viewer: Viewer) -> Outcome:
    """A (scripted, offline) researcher on an outlined object: starts at once, runs in the
    research process, asks for a fresh snapshot, records a finding, finishes, is announced
    as application activity, and its files and session are served."""
    out = Outcome("researcher lifecycle (scripted researcher, no cloud)")
    await stack.seek(2650)
    object_id = await _outline_a_spot(stack, viewer, out, radius=30)
    if object_id is None:
        return out
    notices_before = viewer.notice_count
    since = len(viewer.results)
    t0 = time.time()
    await viewer.send({"type": "tool", "brief": "harness check (scripted)", "object": object_id, "effort": "quick",
                       "fake": {"seconds": 3, "snapshot": True, "finding": "scripted finding", "illustrate": "a scripted illustration"}})
    reply = await viewer.result_for("tool", since)
    out.check("starting it returns at once", reply and reply.get("ok") and time.time() - t0 < 1.0,
              f"{(time.time() - t0) * 1000:.0f} ms, {reply}")
    if not reply or not reply.get("ok"):
        return out
    rid = reply["rid"]
    end = time.time() + 30
    saw_running = False
    while time.time() < end and viewer.research.get(rid, {}).get("status") != "done":
        saw_running |= viewer.research.get(rid, {}).get("status") == "running"
        await asyncio.sleep(0.1)
    session = viewer.research.get(rid, {})
    out.check("it ran in the research process and finished", saw_running and session.get("status") == "done",
              f"{session.get('status')} after {time.time() - t0:.1f} s")
    out.check("its finding and step history came through", bool(session.get("findings")) and bool(session.get("step")),
              f"{len(session.get('findings') or [])} finding(s); last step: {session.get('step')}")
    made = [o for o in session.get("outputs") or [] if o.get("kind") == "image"]
    content = (made[0].get("content") or {}) if made else {}
    out.check("what it made carries readable content", content.get("kind") == "text" and "scripted illustration" in content.get("text", ""),
              str(content)[:100] or "no picture")
    r = await stack.http.get(stack.orch_url + session.get("snapshot", "/none"))
    out.check("the snapshot it started from is served", r.status_code == 200 and r.headers.get("content-type") == "image/jpeg",
              f"{r.status_code} {session.get('snapshot')}")
    detail = (await stack.http.get(f"{stack.orch_url}/api/research/{rid}")).json()
    out.check("the session and its steps are served", detail.get("status") == "done" and len(detail.get("steps", [])) >= 2,
              f"{len(detail.get('steps', []))} steps")
    await asyncio.sleep(0.3)
    original = session.get("resultRun") or {}
    told = viewer.research_activity(rid, original, since=notices_before)
    work_keys = ("id", "kind", "path", "version", "replaces", "content")
    expected_work = [{key: work.get(key) for key in work_keys} for work in session.get("outputs") or []]
    out.check("the result was announced as original application activity",
              len(told) == 1 and told[0]["facts"].get("result") == session.get("result")
              and isinstance(told[0]["facts"].get("result"), dict)
              and [{key: work.get(key) for key in work_keys} for work in told[0]["facts"].get("outputs") or []]
              == expected_work,
              f"{len(told)} received original completions; run {original.get('runId')}")
    return out


async def scenario_correction(stack: Stack, viewer: Viewer) -> Outcome:
    """A (scripted) researcher suggests a different name for an outline: it is announced,
    nothing changes until it is accepted, accepting relabels it; then a follow-up question
    continues the same researcher."""
    out = Outcome("researcher corrections and follow-ups (scripted researcher, no cloud)")
    await stack.seek(2650)
    object_id = await _outline_a_spot(stack, viewer, out, radius=30)
    if object_id is None:
        return out
    t0 = time.time()
    while time.time() - t0 < 5 and object_id not in viewer.scene:
        await asyncio.sleep(0.05)
    before = viewer.scene.get(object_id, {}).get("label")
    chats_before = len(viewer.chats)
    since = len(viewer.results)
    await viewer.send({"type": "tool", "brief": "harness check (scripted)", "object": object_id, "effort": "quick",
                       "fake": {"seconds": 1.5, "correction": {"to_label": "trichome base", "reason": "scripted"}}})
    reply = await viewer.result_for("tool", since)
    rid = (reply or {}).get("rid")
    t0 = time.time()
    while time.time() - t0 < 20 and viewer.research.get(rid, {}).get("status") != "done":
        await asyncio.sleep(0.1)
    session = viewer.research.get(rid, {})
    proposed = [c for c in session.get("corrections") or [] if c["status"] == "proposed"]
    if not out.check("the suggestion arrived", bool(proposed), str(session.get("corrections"))):
        return out
    now = viewer.scene.get(object_id, {}).get("label")
    out.check("nothing changed before it was accepted", now == before, f"{before!r} -> {now!r}")
    # Corrections show on the label and its card, where they can be accepted, not in the
    # chat (the chat is the conversation; brain/narrator.py, since 46330d0).
    told = [c for c in viewer.chats[chats_before:] if "suggests" in c.get("text", "")]
    out.check("it shows on the label, not posted in the chat", not told, told[0]["text"][:100] if told else "not in the chat")
    since = len(viewer.results)
    await viewer.send({"type": "accept-correction", "id": proposed[0]["id"]})
    reply = await viewer.result_for("accept-correction", since)
    await asyncio.sleep(0.3)
    obj = viewer.scene.get(object_id, {})
    out.check("accepting relabels the outline and says who suggested it",
              (reply or {}).get("ok") and obj.get("label") == "trichome base" and obj.get("revisedBy") == session.get("name"),
              f"{obj.get('label')} (revised by {obj.get('revisedBy')})")
    since = len(viewer.results)
    # This no-cloud scenario exercises the registered capability directly. Natural-language
    # intent selection now belongs to Brain and is qualified separately with actual models.
    await viewer.send({"type": "tool", "name": "researcher_followup", "args": {
        "researcher": rid, "question": "is it common on leaves?", "mode": "answer"}})
    reply = await viewer.result_for("tool", since)
    t0 = time.time()
    await asyncio.sleep(0.3)
    while time.time() - t0 < 20 and viewer.research.get(rid, {}).get("status") != "done":
        await asyncio.sleep(0.1)
    session = viewer.research.get(rid, {})
    history = session.get("history") or []
    out.check("a follow-up question is answered by the same researcher, its work kept",
              (reply or {}).get("ok") and session.get("status") == "done" and len(history) == 1
              and history[0].get("mode") == "answer" and bool((history[0].get("result") or {}).get("summary"))
              and bool(session.get("result")),
              f"{session.get('status')}, {len(history)} earlier, {(history[0] if history else {}).get('mode')}")
    return out


FILLER = ("let me check", "let me look", "checking", "one moment", "give me a moment", "let me see", "take a look")


async def scenario_voice_sees(stack: Stack, viewer: Viewer) -> Outcome:
    """Cloud: the voice already sees. "What are we looking at here?" is answered straight away
    from its view notes (no hand-off, no "let me check"), about what is actually in view."""
    out = Outcome("the voice answers what it sees at once (cloud)")
    await stack.seek(2650)
    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    await asyncio.sleep(5.0)  # the app takes its first look after the slide settles
    async with Talk(stack) as talk:
        await asyncio.sleep(3.0)  # the view notes reach the voice
        out.check("it stays quiet until spoken to (view notes are silent)", not talk.audio_at,
                  f"{len(talk.audio_at)} audio chunks: {talk.said_since(0)[:80]}")
        t = await talk.say("What are we looking at here?")
        await talk.quiet(t, after_result=False)
        said = talk.said_since(t)
        first = next((a for a in talk.audio_at if a > t), None)
        calls = [e for at, e in talk.events if at >= t and e["kind"] == "tool_call"]
        if out.check("it answered aloud", first is not None):
            out.check("speech started within 1.5 s", first - t <= 1.5, f"{first - t:.2f} s")
        out.check("without handing over to look", not calls, f"{len(calls)} hand-offs")
        out.check("without filler like \"let me check\"", not any(f in said.lower() for f in FILLER), said[:160])
        out.check("about what is in view", any(w in said.lower() for w in ("leaf", "stoma", "stomata", "cell", "plant")), said[:160])
    return out


async def scenario_voice_magnify(stack: Stack, viewer: Viewer) -> Outcome:
    """Cloud: "magnify that" on an outlined stoma opens the magnifier quickly (the fast pass
    acts from notes, no new look) and the voice does not stall."""
    out = Outcome("magnify by voice, quickly (cloud)")
    await stack.seek(2650)
    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    await asyncio.sleep(4.0)
    since = len(viewer.results)
    await viewer.send({"type": "ask", "text": "Highlight the stoma in the middle"})
    reply = await viewer.result_for("ask", since, timeout=25)
    if not out.check("a stoma was outlined first", reply and reply.get("objectIds"), str((reply or {}).get("actions"))):
        return out
    await viewer.send({"type": "magnifier", "action": "close"})
    await asyncio.sleep(0.5)
    async with Talk(stack) as talk:
        await asyncio.sleep(2.0)
        t = await talk.say("Magnify that.")
        opened = None
        end = time.time() + 15
        while time.time() < end:
            if viewer.magnifier.get("open"):
                opened = time.time()
                break
            await asyncio.sleep(0.05)
        await talk.quiet(t, timeout=15)
        said = talk.said_since(t)
        heard = next((at for at, e in talk.events if at >= t - 2 and e["kind"] == "input_transcript"
                      and not (e.get("data") or {}).get("interim")), None)
        handed = next((at for at, e in talk.events if at >= t - 2 and e["kind"] == "tool_call"), None)
        parts = (f"speech ended, transcript +{heard - t:.2f} s, hand-off +{handed - t:.2f} s"
                 if heard and handed else "")
        if out.check("the magnifier opened", opened is not None):
            out.check("within 2.5 s of the request", opened - t <= 2.5, f"{opened - t:.2f} s; {parts}")
        out.check("without filler", not any(f in said.lower() for f in FILLER), said[:160])
    return out


async def scenario_teach_me(stack: Stack, viewer: Viewer) -> Outcome:
    """Cloud: by voice, "can you teach me more about stomata?" gets a short answer now and a
    promise; a lesson researcher starts; when it is done the lesson comes up beside the view
    by itself and the voice says so."""
    out = Outcome("teach me more: answer now, lesson later, brought up when ready (cloud)")
    await stack.seek(2650)
    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    await asyncio.sleep(4.0)
    async with Talk(stack) as talk:
        await asyncio.sleep(2.0)
        t = await talk.say("Can you teach me more about stomata?")
        await talk.quiet(t, timeout=20)
        said = talk.said_since(t)
        await asyncio.sleep(1.0)
        started = [a for name, a in _actions_since(t, talk).items() if name == "start_researcher"]
        out.check("a lesson researcher started", bool(started) and started[0].get("ok"), str(started[:1]))
        out.check("it said something useful now and promised the lesson", len(said) > 60 and
                  any(w in said.lower() for w in ("lesson", "ready", "put together", "prepar", "making")), said[:220])
        # Only a researcher started by this request counts (not one left from an earlier scenario).
        sessions = [x for x in viewer.research.values() if x.get("purpose") == "research" and x.get("created", 0) >= t - 10]
        rid = max(sessions, key=lambda x: x.get("created", 0))["rid"] if sessions else None
        end = time.time() + 360
        while rid and time.time() < end and viewer.research.get(rid, {}).get("status") in ("queued", "starting", "running"):
            await asyncio.sleep(1.0)
        session = viewer.research.get(rid, {}) if rid else {}
        out.check("the lesson was made", session.get("status") == "done" and any(o.get("kind") == "html" for o in session.get("outputs") or []),
                  f"{session.get('status')}, ${session.get('spent', 0):.2f}")
        done_at = time.time()
        await asyncio.sleep(1.0)
        out.check("it came up beside the view by itself", viewer.presenter.get("open") and viewer.presenter.get("rid") == rid,
                  str(viewer.presenter))
        await talk.quiet(done_at - 0.5, timeout=25, after_result=False)
        said = talk.said_since(done_at - 1)
        out.check("and the voice said it is ready", len(said) > 15, said[:200])
    return out


async def scenario_voice_while_researching(stack: Stack, viewer: Viewer) -> Outcome:
    """Cloud: ask by voice for a researcher, then a live question while it works: the live
    answer comes at normal speed, and the researcher's result is spoken when it lands."""
    out = Outcome("voice stays live while a researcher works (cloud)")
    await stack.seek(2650)
    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    await asyncio.sleep(2.0)
    async with Talk(stack) as talk:
        await asyncio.sleep(1.0)
        t1 = await talk.say("Please start a researcher to look into how guard cells open and close a stoma.")
        await talk.quiet(t1, after_result=False)
        await asyncio.sleep(1.0)
        used = _actions_since(t1, talk)
        started = used.get("start_researcher", {})
        out.check("a researcher was started from voice", started.get("ok") is True, str(started.get("summary") or list(used)))
        active = [s for s in viewer.research.values() if s.get("status") in ("queued", "starting", "running")]
        t2 = await talk.say("What is the oval shape in the middle of the view?")
        busy = [s for s in viewer.research.values() if s.get("status") in ("queued", "starting", "running")]
        await talk.quiet(t2)
        first = next((t for t in talk.audio_at if t > t2), None)
        outlined = [label for r in talk.results_since(t2) for label in (r.get("result") or {}).get("outlined") or []]
        out.check("the researcher was still working when the live question was asked", bool(active) and bool(busy),
                  ", ".join(f"{s['name']} {s['status']}" for s in busy) or "none running")
        if out.check("the live question was answered aloud", first is not None):
            out.check("speech started within 2.5 s, as without a researcher", first - t2 <= 2.5, f"{first - t2:.2f} s")
        pointed = [a.get("summary", "") for a in _actions_since(t2, talk).values()
                   if a.get("tool") in ("select", "zoom", "highlight") and a.get("ok")]
        out.check("and the oval was outlined, or its existing outline pointed at", bool(outlined or pointed),
                  ", ".join(outlined + pointed))
        rid = busy[0]["rid"] if busy else (active[0]["rid"] if active else None)
        end = time.time() + 120
        while rid and time.time() < end and viewer.research.get(rid, {}).get("status") in ("queued", "starting", "running"):
            await asyncio.sleep(0.5)
        session = viewer.research.get(rid, {}) if rid else {}
        out.check("the researcher finished", session.get("status") == "done",
                  f"{session.get('status')}, ${session.get('spent', 0):.3f}, {(session.get('result') or {}).get('one_liner', '')[:100]}")
        done_at = time.time()
        await talk.quiet(done_at - 0.5, timeout=25, after_result=False)
        said = talk.said_since(done_at - 1)
        out.check("its result was spoken when it landed", len(said) > 15, said[:160])
    return out


async def scenario_research_image(stack: Stack, viewer: Viewer) -> Outcome:
    """Cloud: "make an annotated picture of the stoma" starts a researcher that labels the
    object's photo (gpt-image-2.5-flare), the picture is served, and "show it in the
    magnifier" puts it there with its credit."""
    out = Outcome("researcher makes an annotated picture, shown in the magnifier (cloud)")
    await stack.seek(2650)
    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    await asyncio.sleep(2.0)
    since = len(viewer.results)
    await viewer.send({"type": "ask", "text": "Outline the stoma in the middle and make me an annotated picture of it"})
    reply = await viewer.result_for("ask", since, timeout=25)
    used = _tools_used((reply or {}).get("actions"))
    if not out.check("a researcher was started for the picture", used.get("start_researcher", {}).get("ok") is True,
                     str(used.get("start_researcher") or list(used))):
        return out
    rid = max(viewer.research.values(), key=lambda s: s.get("created", 0))["rid"]
    t0 = time.time()
    while time.time() - t0 < 120 and viewer.research.get(rid, {}).get("status") in ("queued", "starting", "running"):
        await asyncio.sleep(0.5)
    session = viewer.research.get(rid, {})
    images = [o for o in session.get("outputs") or [] if o.get("kind") == "image"]
    out.check("it finished with an annotated picture", session.get("status") == "done" and bool(images),
              f"{session.get('status')} in {time.time() - t0:.0f} s, ${session.get('spent', 0):.3f}, {len(images)} picture(s)")
    if images:
        r = await stack.http.get(stack.orch_url + images[0]["url"])
        out.check("the picture is served", r.status_code == 200 and r.headers.get("content-type") == "image/png",
                  f"{r.status_code}, {len(r.content)} bytes; {images[0].get('caption', '')[:80]}")
        items = (images[0].get("content") or {}).get("items") or []
        out.check("its labels are readable with where they point", bool(items) and all(i.get("name") and i.get("where") for i in items),
                  "; ".join(f"{i.get('name')}: {i.get('where')} {i.get('box')}" for i in items)[:160] or "no labels")
    since = len(viewer.results)
    await viewer.send({"type": "ask", "text": "Show me that picture in the magnifier"})
    reply = await viewer.result_for("ask", since, timeout=25)
    await asyncio.sleep(0.3)
    current = (viewer.magnifier.get("sources") or [{}])[viewer.magnifier.get("index", 0)] if viewer.magnifier.get("sources") else {}
    out.check("the magnifier shows it with its credit", viewer.magnifier.get("open") and current.get("kind") == "image"
              and rid in current.get("url", ""), f"{current.get('label')} / {current.get('credit')}")
    return out


async def scenario_descriptions(stack: Stack, viewer: Viewer) -> Outcome:
    """Cloud: outlined, named things get a researched description per kind of thing, within
    the instant budget, and its reference pictures are fetched and served when shown."""
    out = Outcome("descriptions under labels (cloud)")
    await stack.seek(2650)
    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    await asyncio.sleep(2.0)
    since = len(viewer.results)
    await viewer.send({"type": "ask", "text": "Highlight the oval structures"})
    reply = await viewer.result_for("ask", since, timeout=25)
    if not out.check("things were outlined and named", reply and reply.get("objectIds"), str((reply or {}).get("error", ""))):
        return out
    t0 = time.time()
    while time.time() - t0 < 5 and not all(i in viewer.scene for i in reply["objectIds"]):
        await asyncio.sleep(0.05)
    keys = {viewer.scene[i]["labelKey"] for i in reply["objectIds"] if i in viewer.scene}
    if not out.check("the outlines reached the viewer", bool(keys)):
        return out
    t0 = time.time()
    while time.time() - t0 < 40 and not all(viewer.notes.get(k, {}).get("status") in ("ready", "failed") for k in keys):
        await asyncio.sleep(0.25)
    took = time.time() - t0
    ready = {k: viewer.notes.get(k, {}) for k in keys}
    out.check("one description per kind of thing", len(keys) <= len(reply["objectIds"]),
              f"{len(reply['objectIds'])} outlines -> keys {sorted(keys)}")
    out.check("each is ready within 30 s", all(e.get("status") == "ready" for e in ready.values()) and took <= 30,
              f"{took:.1f} s; " + "; ".join(f"{k}: {(e.get('description') or {}).get('title')}" for k, e in ready.items()))
    runs = [viewer.research.get(e.get("rid"), {}) for e in ready.values()]
    spent = [s.get("spent", 0) + s.get("spentEstimated", 0) for s in runs]
    out.check("within the describe budget ($0.08 each, 25 s)", all(x <= 0.08 for x in spent)
              and all(s.get("effort") == "describe" for s in runs), ", ".join(f"${x:.3f}" for x in spent))
    key = next(iter(keys))
    await viewer.send({"type": "reveal", "key": key})
    t1 = time.time()
    while time.time() - t1 < 25 and not viewer.notes.get(key, {}).get("refs"):
        await asyncio.sleep(0.25)
    refs = viewer.notes.get(key, {}).get("refs") or []
    served = [(await stack.http.get(stack.orch_url + r["url"])).status_code for r in refs]
    out.check("its reference pictures are fetched, credited and served", bool(refs) and all(c == 200 for c in served),
              "; ".join(f"{r.get('credit')} ({c})" for r, c in zip(refs, served)) or "none")
    return out


async def _deliverable(stack: Stack, viewer: Viewer, out: Outcome, ask: str, kind: str, minutes: float) -> dict | None:
    await stack.seek(2650)
    await stack.wait(lambda h: h["state"] == "live", 15, "live")
    await asyncio.sleep(2.0)
    since = len(viewer.results)
    t0 = time.time()
    await viewer.send({"type": "ask", "text": ask})
    reply = await viewer.result_for("ask", since, timeout=25)
    used = _tools_used((reply or {}).get("actions"))
    if not out.check("the brain started a researcher for it", used.get("start_researcher", {}).get("ok") is True,
                     str(used.get("start_researcher") or list(used))):
        return None
    out.check("the answer came at live speed", time.time() - t0 < 6, f"{time.time() - t0:.1f} s")
    rid = max(viewer.research.values(), key=lambda s: s.get("created", 0))["rid"]
    while time.time() - t0 < minutes * 60 and viewer.research.get(rid, {}).get("status") in ("queued", "starting", "running"):
        await asyncio.sleep(1.0)
    session = viewer.research.get(rid, {})
    made = [o for o in session.get("outputs") or [] if o.get("kind") == kind]
    out.check(f"it finished with a {kind}", session.get("status") == "done" and bool(made),
              f"{session.get('status')} in {time.time() - t0:.0f} s, {session.get('effort')} effort, ${session.get('spent', 0):.3f} "
              f"of ${session.get('cap', 0):.2f}")
    return made[0] if made else None


async def scenario_research_video(stack: Stack, viewer: Viewer) -> Outcome:
    """Cloud: an explicit request for a video starts a standard researcher that makes a Veo clip
    from the photo, labelled as a generated illustration, served as MP4."""
    out = Outcome("researcher makes a video on request (cloud)")
    made = await _deliverable(stack, viewer, out, "Make me a short video showing how this stoma opens", "video", 4)
    if made:
        r = await stack.http.get(stack.orch_url + made["url"])
        out.check("the video is served as MP4", r.status_code == 200 and r.headers.get("content-type") == "video/mp4",
                  f"{r.status_code}, {len(r.content)} bytes")
        out.check("it is labelled as a generated illustration", "not footage" in made.get("caption", ""), made.get("caption", ""))
    return out


async def scenario_research_lesson(stack: Stack, viewer: Viewer) -> Outcome:
    """Cloud: an explicit request for a lesson makes a self-contained interactive page, served
    under a no-network sandbox."""
    out = Outcome("researcher makes an interactive lesson on request (cloud)")
    made = await _deliverable(stack, viewer, out, "Make me an interactive lesson about how stomata work", "html", 6)
    if made:
        r = await stack.http.get(stack.orch_url + made["url"])
        csp = r.headers.get("content-security-policy", "")
        out.check("the lesson is served under a sandbox with no network", r.status_code == 200 and "sandbox" in csp
                  and "default-src 'none'" in csp, csp[:80])
        body = r.text.lower()
        out.check("it is self-contained (no external scripts or styles)", "<script src=\"http" not in body
                  and "<link" not in body and len(body) > 1500, f"{len(body)} characters")
        steps = (made.get("content") or {}).get("steps") or []
        out.check("its questions and answers are readable, and each has its place in the page (#q1...)",
                  bool(steps) and all(s.get("prompt") and s.get("answer") for s in steps)
                  and all(f'id="{s["id"]}"' in r.text or f"id='{s['id']}'" in r.text for s in steps),
                  "; ".join(f"{s['id']}: {s['prompt'][:40]}" for s in steps)[:160] or "no steps")
    return out


CLOUD_SCENARIOS = {"ask": scenario_ask_and_point, "voice": scenario_voice, "dissect-clicks": scenario_dissect_clicks,
                   "brain-chat": scenario_brain_chat, "voice-tools": scenario_voice_tools, "cross-channel": scenario_cross_channel,
                   "voice-while-researching": scenario_voice_while_researching, "research-image": scenario_research_image,
                   "descriptions": scenario_descriptions, "research-video": scenario_research_video,
                   "research-lesson": scenario_research_lesson, "voice-sees": scenario_voice_sees,
                   "voice-magnify": scenario_voice_magnify, "teach-me": scenario_teach_me}

SCENARIOS = {
    "live": scenario_live_feed,
    "freeze": scenario_freeze,
    "focus-gate": scenario_focus_gate,
    "content-freeze": scenario_content_freeze,
    "still-change": scenario_still_change,
    "crash": scenario_crash,
    "unplug": scenario_unplug_reconnect,
    "unplug-gone": scenario_unplug_unrecoverable,
    "tracking": scenario_outline_tracking,
    "outage-tracking": scenario_outline_across_outage,
    "refind": scenario_refind,
    "identity": scenario_identity,
    "follow-clicks": scenario_follow_clicks,
    "media-load": scenario_media_load,
    "footage-switch": scenario_footage_switch,
    "arena": scenario_arena,
    "magnifier": scenario_magnifier,
    "slide-map": scenario_slide_map,
    "research": scenario_research_fake,
    "lesson-walkthrough": scenario_lesson_walkthrough,
    "revise-lesson": scenario_revise_lesson,
    "threads": scenario_threads,
    "sessions": scenario_sessions,
    "sources": scenario_sources,
    "still-population": scenario_still_population,
    "library": scenario_library,
    "no-camera": scenario_no_camera,
    "actions": scenario_actions,
    "dataset": scenario_dataset,
    "correction": scenario_correction,
    "provenance": scenario_provenance,
    "cells-clicks": scenario_cells_clicks,
    "model-load": scenario_model_load,
    "maintenance-during-live": scenario_maintenance_during_live,
    "live-under-load": scenario_live_under_load,
}
# Long runs, only by name (`--only long-session`): not part of `make sim-test`.
SCENARIOS.update({"cells-population": scenario_cells_population, "cells-then-move": scenario_cells_then_move,
                  "evidence-restart": scenario_evidence_restart})
LONG_SCENARIOS = {"long-session": scenario_long_session}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", help="recording to play (default: the manifest's qualified leaf recording)")
    parser.add_argument("--start-frame", type=int, default=2650)
    parser.add_argument("--bridge-port", type=int, default=18765)
    parser.add_argument("--orch-port", type=int, default=18770)
    parser.add_argument("--attach", action="store_true", help="use a running stack (make sim) on the given ports")
    parser.add_argument("--only", default="", help="comma-separated scenario names: " + ", ".join(SCENARIOS) + ", "
                        + ", ".join(CLOUD_SCENARIOS) + ", " + ", ".join(LONG_SCENARIOS))
    parser.add_argument("--cloud", action="store_true", help="also run scenarios that call cloud AI models (uses API credit)")
    args = parser.parse_args()
    if not args.video:
        try:
            args.video = str(default_video())
        except KeyError:
            parser.error(f"Simulation prerequisite: default recording {DEFAULT_VIDEO_ID!r} is not listed in the asset "
                         "manifest. Restore the qualified manifest and recording, or pass --video for scenarios "
                         "that support another input. See docs/simulation.md.")
    args.video = str(Path(args.video).expanduser().resolve())
    if not Path(args.video).is_file():
        parser.error(f"Simulation prerequisite: recording is missing: {args.video}. Restore it locally, or use "
                     "make assets-fetch if its backup is configured. See docs/simulation.md for scenario requirements.")
    if args.attach and args.bridge_port == 18765:
        args.bridge_port, args.orch_port = int(os.environ.get("CAMERA_PORT", 8765)), 8770

    all_scenarios = {**SCENARIOS, **CLOUD_SCENARIOS, **LONG_SCENARIOS}
    chosen = [s for s in args.only.split(",") if s] or list(SCENARIOS) + (list(CLOUD_SCENARIOS) if args.cloud else [])
    outcomes: list[Outcome] = []
    started = time.time()
    async with Stack(Path(args.video), args.bridge_port, args.orch_port, args.start_frame, args.attach) as stack:
        viewer = Viewer(stack.ws_url)
        viewer_task = asyncio.create_task(viewer.run())
        try:
            await asyncio.sleep(1.5)
            for name in chosen:
                receiver_failures = [viewer.failure]
                if viewer_task.done():  # a scenario restarted the orchestrator: connect again
                    await viewer_task
                    viewer = Viewer(stack.ws_url)
                    viewer_task = asyncio.create_task(viewer.run())
                    await asyncio.sleep(1.5)
                print(f"\n▶ {name}", flush=True)
                try:
                    await viewer.ready()
                    outcome = await all_scenarios[name](stack, viewer)
                except Exception as exc:
                    outcome = Outcome(name)
                    outcome.check("scenario ran", False, f"{type(exc).__name__}: {exc}")
                try:
                    await stack.sim("plug")
                    settled, _ = await stack.wait(lambda h: h["state"] == "live", 20, "settle")
                    if not settled:
                        raise RuntimeError("the stack did not settle after the scenario")
                    await asyncio.sleep(0.2)
                    if viewer_task.done():
                        await viewer_task
                        receiver_failures.append(viewer.failure)
                        viewer = Viewer(stack.ws_url)
                        viewer_task = asyncio.create_task(viewer.run())
                        await asyncio.sleep(1.5)
                    await viewer.send({"type": "clear"})
                    await asyncio.sleep(1.0)
                except Exception as exc:
                    outcome.check("scenario cleanup completed", False, f"{type(exc).__name__}: {exc}")
                receiver_failures.append(viewer.failure)
                for failure in dict.fromkeys(receiver_failures):
                    if failure:
                        outcome.check("observer receiver ran", False, failure)
                if outcome.flight and not outcome.passed:
                    save_flight(name, outcome.flight)
                for c in outcome.checks:
                    print(f"  {'pass' if c.ok else 'FAIL'}  {c.what}{'  (' + c.detail + ')' if c.detail else ''}", flush=True)
                outcomes.append(outcome)
        finally:
            viewer_task.cancel()
            await asyncio.gather(viewer_task, return_exceptions=True)

    passed = sum(o.passed for o in outcomes)
    print(f"\n{passed}/{len(outcomes)} scenarios passed in {time.time() - started:.0f}s (simulated camera; not live hardware)")
    RESULTS.mkdir(exist_ok=True)
    teardown = getattr(stack, "teardown", [])
    teardown_passed = all(item["ok"] for item in teardown)
    if not teardown_passed:
        print("FAIL  stack teardown; child exit and retained logs are in latest.json", flush=True)
    report = {
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "video": Path(args.video).name,
        "teardown": teardown,
        "teardownPassed": teardown_passed,
        "scenarios": [{"name": o.name, "passed": o.passed, "checks": [c.__dict__ for c in o.checks],
                       **({"observations": o.observations} if o.observations else {})} for o in outcomes],
    }
    (RESULTS / "latest.json").write_text(json.dumps(report, indent=2))
    return 0 if passed == len(outcomes) and teardown_passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
