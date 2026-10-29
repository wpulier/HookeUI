"""The orchestrator hub: frames in, perception, scene, and fan-out to browsers.

Threading model: everything that touches shared state runs on the asyncio loop. CPU work
(JPEG decode, motion, candidates, template matching) runs in small thread pools so the loop
keeps accepting frames; model inference runs in the separate GPU worker process.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import struct
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import cv2
import numpy as np

from . import spaces
from .candidates import Candidate, CandidateTracker
from .fov import FieldOfView, FovTracker, edge_radius
from .frames import Frame, FrameSource
from .gpu_client import GpuClient
from .machine import Machine
from .epochs import follow_links
from .motion import MotionTracker
from .options import Settings
from .contract import FEED_STALE_S
from .quality import Content, Quality, StillChange, thumbnail, view_stats
from .scene import Scene, match_at, refind

FRAME_MAGIC = b"MMF1"
# Every frame's facts (offset, epoch, disc, quality) are kept this many frames (30 s at 30 fps; a
# few hundred bytes each), so a box from any look or spoken request still made in that time can be
# mapped to now (2026-10-06: 400 frames, 13 s, and targets failed with "that frame is too old").
INFOS_KEPT = 900
MAX_DRIFT_CORRECTION_PX = 10.0  # largest residual drift the motion tracker accumulates (see docs/bench)
# An outline from another epoch is placed by its template only on a close match. A return to a
# place seen before is rejoined by motion (verified on the whole view, W5); this is the fallback.
# On leaf epidermis a look-alike cell elsewhere matched at 0.65-0.71 (sim `refind`, 1 of 7 runs
# drawn ~100 px off at 0.65), the real one at 0.97-0.99.
REFIND_ACROSS_EPOCHS_SCORE = 0.8
# An outline's own pixels match where the slide's move puts it at least this well to count as in
# place (contract 3); the same score the same-epoch re-anchor accepts a nudge at.
VERIFY_SCORE = 0.6


def _reanchor_state(obj) -> tuple:
    """Everything a template check's placement depends on, separate from the mutable scene object."""
    return (getattr(obj, "revision", 0), getattr(obj, "anchor_frame", None), obj.anchor_epoch,
            tuple(obj.anchor_offset), tuple(tuple(tuple(p) for p in poly) for poly in obj.polygons),
            tuple(obj.template_origin), obj.track, obj.lost_since)


@dataclass(frozen=True)
class _ReanchorSnapshot:
    object: object
    state: tuple
    template: object

    @classmethod
    def take(cls, obj):
        view = copy.copy(obj)
        state = _reanchor_state(obj)
        view.polygons = [[list(p) for p in poly] for poly in obj.polygons]
        if obj.template is not None:
            view.template = obj.template.copy()
        return cls(view, state, obj.template)

    def matches(self, obj) -> bool:
        return (obj is not None and _reanchor_state(obj) == self.state and obj.template is self.template
                and (self.template is None or np.array_equal(obj.template, self.object.template)))


class _ReanchorChange(dict):
    """Existing change dictionaries retain their checked state without adding keys to the scene or wire."""
    def __init__(self, fields: dict, checked: _ReanchorSnapshot):
        super().__init__(fields)
        self.checked = checked


@dataclass
class FrameInfo:
    id: int
    width: int
    height: int
    dx: float
    dy: float
    epoch: int
    tracked: bool
    steady: bool
    fov: FieldOfView | None
    wall_at: float
    focus: float = 0.0  # sharpness of the eyepiece view (variance of the Laplacian, downscaled)
    analysed_at: float = 0.0  # time.time() when disc, motion and focus were done (camera-to-screen stages)
    quality: tuple[str, ...] = ()  # "frozen", "blurry", "empty", "dark", "bright", "glare" (orchestrator/quality.py)
    changed_at: float = 0.0  # wall time the picture last changed at all (a frozen feed keeps arriving)
    change: str | None = None  # in a still view: "content", "light" or "focus" (quality.StillChange)
    stats: tuple[float, ...] = ()  # (focus, mean, std, saturated share) of the eyepiece view (quality.view_stats)
    peak: float = 0.0  # the sharpest this slide has looked recently (quality.Quality.peak), for "sharpness %"
    # The eyepiece disc's radius to a fraction of a pixel (fov.edge_radius), 0 when not measured: the
    # knob never changes it, the phone's lens refocusing does (coach.py tells the two apart by it).
    disc: float = 0.0
    seq: int | None = None  # the bridge's number for the frame (frames.Frame.seq), for datasets
    boot: str = ""  # the bridge's run that numbered it
    optics_json: str | None = None  # immutable intake facts; None only on legacy/synthetic records
    source_image_json: str | None = None  # an owned still's original; never camera/test-footage facts


def focus_score(gray: np.ndarray, fov) -> float:
    """How sharp the view is: variance of the Laplacian inside the eyepiece, at quarter size
    (about a millisecond). Comparable between frames of one session, not across specimens."""
    return view_stats(gray, fov)[0]


def parts_follow(scene, changes: list[tuple[str, dict]], info) -> list[tuple[str, dict]]:
    """A re-anchored object's dissected parts get the same correction and tracking state."""
    out = []
    for object_id, change in changes:
        parent = scene.objects.get(object_id)
        if parent is None:
            continue
        for part in [o for o in scene.objects.values() if o.parent == object_id]:
            if "polygons" not in change:
                out.append((part.id, {k: v for k, v in change.items() if k in ("track", "lost_since")}))
                continue
            # The parent moved from its old anchor to `info` plus a template correction;
            # the part (anchored at its own frame) moves by the same correction.
            old_shift = (info.dx - parent.anchor_offset[0], info.dy - parent.anchor_offset[1])
            new_poly = change["polygons"][0][0]
            correction = (new_poly[0] - (parent.polygons[0][0][0] + old_shift[0]),
                          new_poly[1] - (parent.polygons[0][0][1] + old_shift[1]))
            total = (info.dx - part.anchor_offset[0] + correction[0], info.dy - part.anchor_offset[1] + correction[1])
            out.append((part.id, {
                "polygons": [[[x + total[0], y + total[1]] for x, y in poly] for poly in part.polygons],
                "anchor_frame": info.id, "anchor_epoch": info.epoch, "anchor_offset": (info.dx, info.dy),
                "track": change.get("track", part.track), "lost_since": change.get("lost_since"),
            }))
    return out


class Client:
    """One browser connection. Video frames are latest-wins (a slow browser skips frames
    instead of falling behind); everything else (chat, scene, results, research) is sent in
    order and never dropped, and always goes out ahead of the next frame."""

    CONTROL_LIMIT = 512

    def __init__(self, websocket) -> None:
        self.websocket = websocket
        self.frames: asyncio.Queue = asyncio.Queue(maxsize=2)
        self.control: asyncio.Queue = asyncio.Queue()
        self._wake = asyncio.Event()
        self.show_candidates = False
        self.control_overflow = 0  # messages lost because a stuck browser stopped reading

    def offer(self, message, lossy: bool | None = None) -> None:
        """Queue a message. Frames (bytes) and `lossy` debug messages may be replaced by newer
        ones; control messages are kept in order."""
        if lossy if lossy is not None else isinstance(message, bytes):
            if self.frames.full():
                try:
                    self.frames.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            self.frames.put_nowait(message)
        else:
            if self.control.qsize() >= self.CONTROL_LIMIT:
                # Only a browser that stopped reading gets here; keep the newest state.
                self.control.get_nowait()
                self.control_overflow += 1
            self.control.put_nowait(message)
        self._wake.set()

    async def next_message(self):
        """Control messages first, then the latest frame."""
        while True:
            if not self.control.empty():
                return self.control.get_nowait()
            if not self.frames.empty():
                return self.frames.get_nowait()
            self._wake.clear()
            await self._wake.wait()


class Hub:
    view_held = False  # not covered unless the studio says so (set in __init__; commands.py `view`)

    def __init__(self, bridge_url: str, use_gpu: bool = True) -> None:
        self.source = FrameSource(bridge_url, self._on_frame)
        self.fov = FovTracker()
        self.motion = MotionTracker()
        self.quality = Quality()  # each frame's blurry/empty/dark/... flags (orchestrator/quality.py)
        self.content = Content()  # when the picture last changed at all
        self.machine = Machine()  # how the computer is doing, for the studio's performance meter
        self.machine.workers = self.worker_processes
        self.loop_lag = None  # looplag.LoopLag, set by App.build: how late the event loop runs
        self.loads = None  # loads.Loads, set by App.build: models loading on the workers, said and kept within memory
        self.still_change = StillChange()  # something new in a still view (Sight looks again)
        self.on_still_change: list = []  # (kind, FrameInfo) -> None
        self.on_frame: list = []  # (FrameInfo) -> None, for every published frame (cheap listeners only)
        self.last_change: tuple[str, float] | None = None
        self.candidates = CandidateTracker()
        self.scene = Scene()
        self.settings = Settings()
        self.gpu = GpuClient() if use_gpu else None
        self.gpu_heavy = GpuClient("micromind-gpu-heavy", warm=False) if use_gpu else None
        # Molmo 2 (pointing "molmo", orchestrator/molmo.py): its own process, started on first use, so
        # a click, a tracking step or an embedding never waits behind its seconds-long answers.
        self.gpu_molmo = GpuClient("micromind-gpu-molmo", warm=False) if use_gpu else None
        # The follower's own worker (contract 5): EdgeTAM's video state lives there, so a cell run on the
        # analysis worker never stalls following, and following never queues in front of a click.
        self.gpu_follow = GpuClient("micromind-gpu-follow", warm=False) if use_gpu else None
        # Big models' workers of their own (catalog/models: host "own" or "own-python"), made on first use by
        # host_for: a load there blocks no other model's requests, and letting the model go stops its process (R4 of
        # docs/design/2026-10-11-model-runtime.md). MICROMIND_OWN_HOSTS=0 keeps them on the analysis worker.
        self.hosts: dict[str, GpuClient] = {}
        self._client_class = GpuClient  # what its other workers are (a test's stand-in too): its hosts are the same
        self.host_hooks: list = []  # (client) -> None for each host made (App.build: its problems said, its loads told)
        self.own_hosts = bool(use_gpu) and os.environ.get("MICROMIND_OWN_HOSTS", "1") != "0"
        self.follower = None  # objtrack.ObjectFollower (option TRACK "objects"), set by App.build
        from .trail import Trails

        self.trails = Trails()  # where each followed outline went on its own (trail.py), filled by the follower
        self.gpu_error = ""
        self.focused: str | None = None  # the object the user last lingered on
        self.clients: set[Client] = set()
        # The studio covers the live view to look back at the session (its returning card; commands.py `view`): while
        # held, the running look makes no looks and the subject tracker takes none, so nothing new is written into a
        # session the user is only looking back at. Cleared by the studio, by another session's opening and when the
        # last studio leaves (server.py).
        self.view_held = False
        self.infos: OrderedDict[int, FrameInfo] = OrderedDict()
        self.latest_info: FrameInfo | None = None
        self.scene.geometry_at = self._anchor_geometry
        from .threads import raise_this_thread

        # The frame path's one analysis thread, in the frame path's scheduling class (threads.py).
        self._analysis = ThreadPoolExecutor(1, thread_name_prefix="analysis", initializer=raise_this_thread)
        self._background = ThreadPoolExecutor(2, thread_name_prefix="background")
        self._analysis_busy = False
        self._pending_frames: list[Frame] = []
        self._tasks: list[asyncio.Task] = []
        self._sent_scene_version = -1
        self._last_drift_check = 0.0
        self._rejoins_seen = 0  # motion.rejoins carried over (_carry_over)
        self.epoch_links: dict[int, tuple[int, tuple[float, float]]] = {}  # epoch left -> (epoch rejoined, delta)
        # When the view last left each motion epoch (wall time of the first frame after it): when continuity was
        # lost for what was placed in it (identity.py says "continuity was lost at" this; review SF4 of K2).
        self.epoch_left: dict[int, float] = {}
        # An outline brought back after a gap or a jump, with how its place was found again and that match's
        # score (`carried`): identity.py records it as a same-object link with that basis (review B3 of K2).
        self.on_carry: list = []  # (object id, basis, confidence) -> None, on the event loop, never per frame
        self._rejoined: dict[str, float] = {}  # outlines a rejoin carried over, until their own pixels match
        self._steady_frames = 0  # consecutive still frames, for re-anchoring decisions

    # --- lifecycle -------------------------------------------------------------------
    async def start(self) -> None:
        self.source.start()
        self._tasks = [
            asyncio.create_task(self._candidate_loop()),
            asyncio.create_task(self._reanchor_loop()),
            asyncio.create_task(self._scene_push_loop()),
        ]
        if self.gpu is not None:
            self._tasks.append(asyncio.create_task(self._start_gpu()))

    async def _start_gpu(self) -> None:
        try:
            await self.gpu.start()
        except Exception as exc:
            self.gpu_error = f"{type(exc).__name__}: {exc}"

    async def stop(self) -> None:
        await asyncio.to_thread(self.machine.stop)
        layer = getattr(self, 'track_layer', None)
        if layer is not None:
            layer.retire_live()
        for task in self._tasks:
            task.cancel()
        await self.source.stop()
        if self.gpu is not None:
            self.gpu.stop()
        if self.gpu_heavy is not None and self.gpu_heavy.ready:
            self.gpu_heavy.stop()
        if self.gpu_molmo is not None and self.gpu_molmo.ready:
            self.gpu_molmo.stop()
        if self.gpu_follow is not None and self.gpu_follow.ready:
            self.gpu_follow.stop()
        for host in list(self.hosts.values()):
            if host.ready:
                host.stop()

    # --- frames ----------------------------------------------------------------------
    async def _on_frame(self, frame: Frame) -> None:
        # Optional current-camera work belongs to the incarnation visible at
        # intake, never a display opened while this frame's CPU work awaited.
        layer = getattr(self, 'track_layer', None)
        frame._motion_work = layer.analysis_work() if layer is not None else None
        frame._source_image_json = getattr(self, "stage_image_json", None)
        # Freeze optics before the first await, including frames arriving while analysis is busy.
        # JSON owns its nested provenance; later knob/calibration changes cannot mutate it.
        if not hasattr(frame, "_optics_json"):
            try:
                frame._optics_json = json.dumps(self.scale.facts(current_only=True), separators=(",", ":"))
            except Exception:
                frame._optics_json = "{}"  # unavailable at intake stays unavailable for this frame
        # How often feature matches vote on the slide's move (option MOTION_ORB_EVERY), as set now.
        try:
            self.motion.orb_every = int(self.settings.orb_every)
        except (AttributeError, TypeError, ValueError):
            self.motion.orb_every = 1
        # Motion must see frames in order; if analysis falls behind, analyse the backlog in
        # one batch rather than queueing unboundedly.
        self._pending_frames.append(frame)
        if self._analysis_busy:
            return
        self._analysis_busy = True
        # The reader (frames.FrameSource) waits for this: a reader that is behind is sent only the bridge's newest
        # frame, so under load frames are skipped at the bridge and the delay stays bounded. Reading on while the
        # analysis runs was measured worse (docs/bench/priority.md, "The camera bridge's frame path").
        batch = []
        try:
            while self._pending_frames:
                batch, self._pending_frames = self._pending_frames, []
                loop = asyncio.get_running_loop()
                analysed = await loop.run_in_executor(self._analysis, self._analyse_batch, batch)
                for frame_, info in analysed:
                    self._publish(frame_, info)
        except BaseException:
            # The executor may finish after its awaiting callback is cancelled.
            # Retire only transient optional motion ownership, not JPEG facts.
            for abandoned in batch + self._pending_frames:
                self._forget_motion(abandoned, abandoned=True)
            raise
        finally:
            self._analysis_busy = False

    def _analyse_batch(self, frames: list[Frame]) -> list[tuple[Frame, FrameInfo]]:
        # Rejected JPEGs cannot shift a later frame's facts onto their bytes.
        analysed = []
        for frame in frames:
            gray = cv2.imdecode(np.frombuffer(frame.jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
            if gray is None:
                self._forget_motion(frame)
                continue
            fov = self.fov.update(gray)
            sample = self.motion.update(frame.id, gray, fov)
            focus, mean, std, saturated = view_stats(gray, fov)
            changed_at = self.content.update(thumbnail(gray, fov), frame.wall_at)
            flags = self.quality.flags(focus, mean, std, saturated, sample.epoch)
            change = self.still_change.update(gray, fov, sample.steady, sample.epoch, (sample.dx, sample.dy), focus,
                                              frame.wall_at)
            info = FrameInfo(frame.id, gray.shape[1], gray.shape[0], sample.dx, sample.dy, sample.epoch,
                          sample.tracked, sample.steady, fov, frame.wall_at, focus, time.time(), flags, changed_at, change,
                          (focus, mean, std, saturated), self.quality.peak, edge_radius(gray, fov) or 0.0,
                          seq=frame.seq, boot=frame.boot, optics_json=getattr(frame, "_optics_json", "{}"),
                          source_image_json=getattr(frame, "_source_image_json", None))
            claim = getattr(frame, '_motion_work', None)
            try:
                result = claim[0].analyse(gray, info, claim[1]) if claim is not None else None
            except Exception:
                # Optional observations cannot suppress the camera JPEG. No
                # sample is published when its bounded calculation fails.
                result = None
            if claim is not None:
                with claim[0].lock:
                    if not getattr(frame, '_motion_abandoned', False):
                        frame._motion_result = result
            analysed.append((frame, info))
        return analysed

    @staticmethod
    def _forget_motion(frame, *, abandoned=False):
        """Ring Frames keep pixels/facts, never a completed or cancelled motion job.

        The original producer lock closes the cancellation/result-publication
        race with its CPU worker. No later completion can restore detached rows.
        """
        claim = getattr(frame, '_motion_work', None)
        if claim is not None:
            with claim[0].lock:
                if abandoned:
                    frame._motion_abandoned = True
                frame.__dict__.pop('_motion_work', None)
                frame.__dict__.pop('_motion_result', None)
        else:
            if abandoned:
                frame._motion_abandoned = True
            frame.__dict__.pop('_motion_result', None)

    def _anchor_geometry(self, frame_id):
        """Compact trusted anchor facts, retained by Scene independently of frame-ring eviction."""
        from .capture import captures
        from .scene import anchor_geometry

        store = captures(self)
        view = store.get(frame_id)
        if view is None:
            view = self.infos.get(frame_id)
        if view is None:
            return None
        scale = getattr(view, "scale", None) if hasattr(view, "scale") else store.scale_of(view)
        return anchor_geometry(view, scale)

    def _carry_over(self) -> None:
        """A rejoin of a place seen before (motion.py, W5) relates the epoch it leaves to the one it
        rejoins by a verified translation (`epoch_links`). Outlines anchored in an epoch a rejoin left
        are carried over by it, unverified: they are drawn again only once the re-anchor check finds
        their own pixels there (contract 3: "a rejoin"). That is every such outline, whenever it is
        written: those made since continuity was lost and there when the rejoin is seen, and those that
        land after it, made on a view of the epoch left (a click's outline still being segmented while
        the rejoin was confirmed: sim `arena` after `footage-switch`, an arm landed 0.8-1 s after the
        click in the epoch left and was never drawn), or written back there by a check that started
        before it. Nothing is carried past the epoch the view is in now, or out of it. Runs with every
        published frame (a dictionary look-up per outline once a rejoin happened). Populations follow
        the same links (`epoch_links`, population.Layer)."""
        rejoins = getattr(self.motion, "rejoins", [])
        while self._rejoins_seen < len(rejoins):
            r = rejoins[self._rejoins_seen]
            self._rejoins_seen += 1
            if r.get("delta") is not None:
                self.epoch_links[r["from"]] = (r["to"], r["delta"])
        if not self.epoch_links:
            return
        latest = getattr(self, "latest_info", None)
        now = latest.epoch if latest is not None else None
        for obj in list(self.scene.objects.values()):
            epoch, dx, dy = follow_links(self.epoch_links, obj.anchor_epoch, *obj.anchor_offset, now=now)
            if epoch != obj.anchor_epoch:
                self.scene.update(obj.id, anchor_epoch=epoch, anchor_offset=(dx, dy), track="refinding",
                                  lost_since=obj.lost_since or time.time())
                self._rejoined[obj.id] = time.time()  # its carry is said once its own pixels match (`carried`)

    def carried(self, object_id: str, basis: str, confidence: float) -> None:
        """An outline brought back by its own pixels after they had not matched (basis "gap"), after the view
        jumped ("re-find", a template match across epochs) or by the follower ("follower"); one a rejoin carried
        over is "rejoin". Told to `on_carry` (identity.py records it with its basis and the match's score:
        review B3 of K2). Called from the re-anchor loop and the follower's writes, never on the frame path."""
        if self._rejoined.pop(object_id, None) is not None:
            basis = "rejoin"
        for listener in list(self.on_carry):
            try:
                listener(object_id, basis, float(confidence))
            except Exception:
                pass

    def _publish(self, frame: Frame, info: FrameInfo) -> None:
        try:
            self._publish_frame(frame, info)
        finally:
            self._forget_motion(frame)

    def _publish_frame(self, frame: Frame, info: FrameInfo) -> None:
        from .capture import RUN, captures
        previous = self.latest_info
        speed = 0.0
        if previous is not None and previous.epoch == info.epoch:
            speed = float(np.hypot(info.dx - previous.dx, info.dy - previous.dy))
        elif previous is not None:
            self.epoch_left[previous.epoch] = info.wall_at  # continuity lost (or a rejoin) at this frame
        self.infos[info.id] = info
        while len(self.infos) > INFOS_KEPT:
            self.infos.popitem(last=False)
        self.latest_info = info
        # The exact picture's Capture owns its intake optics. Snapshot before listeners
        # can change controls; neither rounded display coordinates nor Scale.wire is its basis.
        captured = captures(self).adopt(info, frame.jpeg)
        scale = copy.deepcopy(captured.scale)
        fov = captured.fov
        frame_basis = {"schema": "frame-basis-v1", "frameId": captured.id, "captureId": None,
                       "run": RUN, "boot": captured.boot, "seq": captured.seq,
                       "epoch": captured.epoch, "tracked": captured.tracked,
                       "imageSize": [captured.width, captured.height],
                       "disc": [fov.cx, fov.cy, fov.radius] if fov is not None else None,
                       "offset": [captured.dx, captured.dy],
                       "objective": scale.get("objective") if scale is not None else None,
                       "umPerPx": scale.get("umPerPx") if scale is not None else None,
                       "scaleId": None, "scale": scale}
        motion_wire = None
        layer = getattr(self, 'track_layer', None)
        if layer is not None:
            try:
                motion_wire = layer.publish_live(getattr(frame, '_motion_result', None), captured, frame_basis)
            except Exception:
                pass  # a refused optional sample never prevents the original camera packet
        self._carry_over()  # after latest_info: carried up to the epoch this frame is in, never out of it
        self._steady_frames = self._steady_frames + 1 if info.steady else 0
        for listener in list(self.on_frame):
            try:
                listener(info)
            except Exception:
                pass
        if info.change is not None:
            self.last_change = (info.change, info.wall_at)
            for listener in list(self.on_still_change):
                try:
                    listener(info.change, info)
                except Exception:
                    pass
        header = {
            "type": "frame",
            "id": info.id,
            "run": RUN,
            "boot": info.boot,
            "seq": info.seq,
            "w": info.width,
            "h": info.height,
            "dx": round(info.dx, 2),
            "dy": round(info.dy, 2),
            "epoch": info.epoch,
            "tracked": info.tracked,
            "steady": info.steady,
            "speed": round(speed, 1),  # px/frame; the studio fades outlines during fast pans
            "q": list(info.quality),  # frozen, blurry, empty, dark, bright, glare (orchestrator/quality.py)
            # Seconds the picture has not changed at all: the studio dims it after frameDimMs
            # (contract/overlay.json), so a frozen feed that still arrives never looks live.
            "still": round(max(0.0, info.wall_at - info.changed_at), 2) if info.changed_at else 0.0,
            "t": info.wall_at,  # arrived from the camera bridge
            "ba": getattr(frame, "bridge_at", None),  # when the bridge took it (X-Frame-At): true delay runs from here
            "an": info.analysed_at or None,  # disc, motion and focus done
            "sent": time.time(),  # handed to the studios (the studio adds when it painted it)
            "fov": None if info.fov is None else [round(info.fov.cx, 1), round(info.fov.cy, 1), round(info.fov.radius, 1)],
            "frameBasis": frame_basis,
        }
        if motion_wire is not None:
            header['motionTracks'] = motion_wire
        encoded = json.dumps(header).encode()
        message = FRAME_MAGIC + struct.pack(">I", len(encoded)) + encoded + frame.jpeg
        for client in list(self.clients):
            client.offer(message)

    # --- coordinates -----------------------------------------------------------------
    def map_point(self, x: float, y: float, from_frame: int, to_frame: int) -> tuple[float, float] | None:
        a, b = self.infos.get(from_frame), self.infos.get(to_frame)
        if a is None or b is None or a.epoch != b.epoch:
            return None
        return x + b.dx - a.dx, y + b.dy - a.dy

    def candidates_in(self, frame_id: int) -> list[Candidate]:
        """Current candidates expressed in the coordinates of `frame_id`."""
        info = self.infos.get(frame_id)
        if info is None:
            return []
        out = []
        for c in self.candidates.current:
            out.append(Candidate(c.id, c.sx + info.dx, c.sy + info.dy, c.radius, c.contrast, c.score, frame_id, c.sx, c.sy, c.hits))
        return out

    def gray(self, frame_id: int) -> np.ndarray | None:
        frame = self.source.get(frame_id)
        if frame is None:
            return None
        return cv2.imdecode(np.frombuffer(frame.jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)

    # --- background loops ------------------------------------------------------------
    async def _candidate_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(0.2)
            info = self.latest_info
            if info is None or not self.source.is_fresh():
                continue
            frame = self.source.get(info.id)
            if frame is None:
                continue
            # Decoded on the background thread with the update: a full-frame decode on the event loop five times a
            # second held back the frame reader (the audit of 2026-10-12).
            found = await loop.run_in_executor(self._background, self._candidates_from, frame.jpeg, info)
            message = json.dumps({
                "type": "candidates",
                "space": spaces.of_wire("candidates"),
                "frameId": info.id,
                "items": [{"id": c.id, "x": round(c.x, 1), "y": round(c.y, 1), "r": round(c.radius, 1), "dark": c.contrast < 0} for c in found],
            })
            for client in list(self.clients):
                if client.show_candidates:
                    client.offer(message, lossy=True)

    def _candidates_from(self, jpeg: bytes, info) -> list:
        gray = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
        return self.candidates.update(info.id, gray, info.fov, (info.dx, info.dy)) if gray is not None else []

    def _reanchor_from(self, checked: list, jpeg: bytes, info, settled: bool = True) -> list:
        gray = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
        return self._reanchor(checked, gray, info, settled) if gray is not None else []

    async def _reanchor_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(0.3)
            info = self.latest_info
            if info is None or not self.source.is_fresh() or not self.scene.objects:
                continue
            # A coordinate correction still needs half a second of stillness: a pan's offset
            # may not be loop-closed yet. A hidden outline can be verified at its unchanged
            # predicted place sooner, using its own pixels and no search/nudge (ADR 0010).
            # Focus recovery can make the whole view's steady flag flicker after those pixels
            # already match, so waiting for global stillness needlessly kept it hidden.
            settled = self._steady_frames >= 15
            now = time.monotonic()
            drift_check = now - self._last_drift_check > 1.0
            # Dissected parts are not matched on their own: they move with their object.
            # Lost ones whose epoch came back (motion rejoined it) are due too; outlines EdgeTAM placed
            # just now (option TRACK "objects") are where it saw them.
            followed = self.follower.placed_recently if self.follower is not None else (lambda oid: False)
            due = [o for o in self.scene.objects.values()
                   if o.kind != "part" and not followed(o.id)
                   and (o.anchor_epoch != info.epoch or o.track in ("lost", "refinding")
                        or (drift_check and o.template is not None))]
            if not settled:
                due = [o for o in due if info.tracked and o.anchor_epoch == info.epoch
                       and o.track in ("lost", "refinding") and o.template is not None]
            if not due:
                continue
            if settled and drift_check:
                self._last_drift_check = now
            frame = self.source.get(info.id)
            if frame is None:
                continue
            # Snapshot on the event loop before the worker starts: a repair or follower may mutate the scene
            # while the worker is queued, matching, or returning. That match certifies only this exact state.
            checked = [_ReanchorSnapshot.take(obj) for obj in due]
            changes = await loop.run_in_executor(self._background, self._reanchor_from, checked, frame.jpeg, info, settled)
            self._apply_reanchor(changes, info)

    def _apply_reanchor(self, changes: list[tuple[str, dict]], info: FrameInfo) -> None:
        """The re-anchor check's changes written to the scene (parts with their objects); an outline it brought back
        after a gap or a jump is then said, with its basis and the match's score (`carried`)."""
        valid = [(oid, dict(change)) for oid, change in changes
                 if not isinstance(change, _ReanchorChange) or change.checked.matches(self.scene.objects.get(oid))]
        carried = [(object_id, change.pop("carried")) for object_id, change in valid if "carried" in change]
        for object_id, change in valid + parts_follow(self.scene, valid, info):
            self.scene.update(object_id, **change)
        for object_id, (basis, score) in carried:
            self.carried(object_id, basis, score)

    def _reanchor(self, objects, gray: np.ndarray, info: FrameInfo, settled: bool = True) -> list[tuple[str, dict]]:
        changes = []
        for item in objects:
            checked = item if isinstance(item, _ReanchorSnapshot) else _ReanchorSnapshot.take(item)
            obj = checked.object

            def keep(change):
                changes.append((obj.id, _ReanchorChange(change, checked)))

            if obj.anchor_epoch is None:
                # Made on a view read back from another run of the app (Captures.read_back: no epoch). Its offsets
                # are that run's (they start again every run), so a search near them finds a lookalike neighbour as
                # readily as the thing (finding 47): never re-found, placed or carried; kept unverified, as an
                # outline not found in another epoch is (the K2 re-check, SF2).
                lost_since = obj.lost_since or time.time()
                track = "lost" if time.time() - lost_since > 4.0 else "refinding"
                if track != obj.track or obj.lost_since is None:
                    keep({"track": track, "lost_since": lost_since})
                continue
            same_epoch = obj.anchor_epoch == info.epoch
            shift = (info.dx - obj.anchor_offset[0], info.dy - obj.anchor_offset[1])
            cx, cy = obj.center
            predicted_center = (cx + shift[0], cy + shift[1])
            if info.fov is not None and not info.fov.contains(*predicted_center, margin=-10):
                if obj.track != "out_of_view":
                    keep({"track": "out_of_view"})
                continue
            if obj.template is None:
                # Nothing to check its place with (ADR 0010): it is never made "tracking" again by
                # its epoch alone; in another epoch it is lost.
                if not same_epoch and obj.track != "lost":
                    keep({"track": "lost"})
                continue
            origin = (obj.template_origin[0] + shift[0], obj.template_origin[1] + shift[1])
            if not settled:
                if same_epoch and info.tracked and obj.track in ("lost", "refinding"):
                    score = match_at(gray, obj.template, origin)
                    if score >= VERIFY_SCORE:
                        keep({"track": "tracking", "lost_since": None, "carried": ("gap", float(score))})
                continue  # never alter geometry or rejoin another epoch before the view settles
            if same_epoch:
                # Motion tracking is already within a few pixels here; only accept a small
                # nudge. A larger "correction" means the template matched a look-alike
                # neighbour, not that the tracker is 40 px off.
                found = refind(gray, obj.template, origin, search_px=40, min_score=0.6)
                if found is not None and np.hypot(found[0], found[1]) > MAX_DRIFT_CORRECTION_PX:
                    found = None
            else:
                found = refind(gray, obj.template, origin, search_px=90, min_score=REFIND_ACROSS_EPOCHS_SCORE)
            if found is None:
                if not same_epoch:
                    lost_since = obj.lost_since or time.time()
                    track = "lost" if time.time() - lost_since > 4.0 else "refinding"
                    if track != obj.track or obj.lost_since is None:
                        keep({"track": track, "lost_since": lost_since})
                    continue
                # Same epoch, no unique correction: is it where the slide's move puts it? Its own
                # pixels decide (contract 3); a failed match makes it unverified (not drawn) until
                # a later check finds it, never "moved" (focus, occlusion and a poor frame fail too).
                score = match_at(gray, obj.template, origin)
                in_place = score >= VERIFY_SCORE
                if in_place and obj.track in ("lost", "refinding"):
                    # Back after a gap: carried by its own pixels, said with that match (review B3 of K2).
                    keep({"track": "tracking", "lost_since": None, "carried": ("gap", float(score))})
                elif not in_place and obj.track == "tracking":
                    keep({"track": "refinding", "lost_since": time.time()})
                continue
            ddx, ddy, score = found
            total = (shift[0] + ddx, shift[1] + ddy)
            if same_epoch and abs(ddx) < 1.0 and abs(ddy) < 1.0 and obj.track == "tracking":
                continue  # motion tracking is already accurate here
            # The template keeps the pixels captured when the object was created, so a bad
            # match can never re-train it; only its position moves with the outline.
            change = {
                "polygons": [[[x + total[0], y + total[1]] for x, y in poly] for poly in obj.polygons],
                "template_origin": (obj.template_origin[0] + total[0], obj.template_origin[1] + total[1]),
                "anchor_frame": info.id,
                "anchor_epoch": info.epoch,
                "anchor_offset": (info.dx, info.dy),
                "track": "tracking",
                "lost_since": None,
            }
            if not same_epoch:  # re-found by its template in another epoch (at least REFIND_ACROSS_EPOCHS_SCORE)
                change["carried"] = ("re-find", float(score))
            elif obj.track in ("lost", "refinding"):  # back after a gap in its own epoch
                change["carried"] = ("gap", float(score))
            keep(change)
        return changes

    async def _scene_push_loop(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            self._push_trails()
            if self.scene.version == self._sent_scene_version:
                continue
            self._sent_scene_version = self.scene.version
            self.broadcast({"type": "scene", **self.scene.to_wire()})

    def _push_trails(self) -> None:
        """Followed outlines' trails, when they changed, at most every trail.PUSH_EVERY_S; a removed
        outline's trail goes with it."""
        self.trails.keep_only(self.scene.objects)
        if not self.trails.due():
            return
        self.trails.sent()
        self.broadcast(self.trails.wire(self.scale_reading()))

    def broadcast(self, payload: dict) -> None:
        message = json.dumps(payload)
        for client in list(self.clients):
            client.offer(message)

    def broadcast_execution(self, payload: dict) -> None:
        """Optional inspection hints must never evict chat/results or fill a stuck studio's
        control queue. Skipped hints remain inspectable through /api/execution."""
        if not self.clients:
            return
        message = json.dumps(payload)
        for client in tuple(self.clients):
            if client.control.qsize() < 8:
                client.offer(message)

    def scale_reading(self):
        """The day's scale for the eyepiece disc now (orchestrator/scale.py), or None."""
        scale, info = getattr(self, "scale", None), self.latest_info
        if scale is None or info is None or info.fov is None:
            return None
        return scale.reading(info.fov.radius)

    def size_of(self, obj) -> dict | None:
        """An outline's size in µm (longest, shortest, area) once the scale is known."""
        from .scale import sizes

        reading = self.scale_reading()
        if reading is None or obj.kind not in ("highlight", "part") or not obj.polygons:
            return None
        return sizes(obj.polygons, reading) or None

    def gpu_clients(self) -> list:
        return [c for c in (getattr(self, "gpu", None), getattr(self, "gpu_heavy", None), getattr(self, "gpu_follow", None),
                            getattr(self, "gpu_molmo", None), *getattr(self, "hosts", {}).values()) if c is not None]

    def host_for(self, model: str | None):
        """The worker an analysis model runs on: one of its own when its manifest's host is "own" or "own-python" (made
        on first use, started on its first call), else the shared analysis worker (the click worker without one)."""
        from gpu_worker.manifests import get

        shared = self.gpu_heavy if self.gpu_heavy is not None else self.gpu
        manifest = get(model)
        if not getattr(self, "own_hosts", False) or manifest is None or manifest.host == "shared" \
                or "analysis" not in manifest.workers:
            return shared
        client = self.hosts.get(manifest.id)
        if client is None:
            client = self.hosts[manifest.id] = self._client_class(f"micromind-gpu-{manifest.id}", warm=False, role="heavy")
            client.own = manifest.id
            for hook in self.host_hooks:
                hook(client)
        return client

    def worker_processes(self) -> list[dict]:
        """Our GPU workers' processes (and what they started) with their roles and what is loading there:
        the meter samples them every tick (machine.py). Read from the machine's thread: only attributes."""
        out = []
        for client in self.gpu_clients():
            try:
                out += client.processes()
            except Exception:
                continue
        return out

    def _models_wire(self) -> dict:
        return _models_health(self)

    def _workers_wire(self) -> list[dict]:
        out = []
        for client in self.gpu_clients():
            wire = getattr(client, "wire", None)
            if callable(wire):
                try:
                    out.append(wire())
                except Exception:
                    continue
        return out

    def _follow_wire(self) -> dict | None:
        follower = getattr(self, "follower", None)
        if follower is None:
            return None
        return {"steps": getattr(follower, "steps", 0), "lastStepAt": round(getattr(follower, "last_step_at", 0.0), 3),
                "followed": len(getattr(follower, "followed", {})),
                "trailStaleBreaks": getattr(getattr(self, "trails", None), "stale_breaks", 0)}

    def _captures_wire(self) -> dict:
        from .capture import captures
        from .ticket import open_tickets

        return {**captures(self).wire(), "openRequests": open_tickets()}

    def best_frame(self, window_s: float = 1.0, epoch: int | None = None):
        """The frame of the last `window_s` seconds best to look at: in the latest (or the given)
        motion epoch, still in the picture buffer, preferring frames that can be named
        (quality.py), then steady ones, then the sharpest. None if there is no frame."""
        from .quality import unnameable

        latest = self.latest_info
        if latest is None:
            return None
        epoch = latest.epoch if epoch is None else epoch
        candidates = [info for fid, info in list(self.infos.items())
                      if info.epoch == epoch and latest.wall_at - info.wall_at <= window_s and self.source.get(fid) is not None]
        if not candidates:
            return latest if self.source.get(latest.id) is not None else None
        return max(candidates, key=lambda i: (not unnameable(i.quality), i.steady, i.focus))

    def health(self) -> dict:
        info = self.latest_info
        source = self.source.health()
        return {
            "service": "microscope-mind-orchestrator",
            **source,
            "motion": None if info is None else {"epoch": info.epoch, "tracked": info.tracked, "dx": round(info.dx, 1), "dy": round(info.dy, 1)},
            "fov": None if info is None or info.fov is None else {"cx": round(info.fov.cx), "cy": round(info.fov.cy), "r": round(info.fov.radius)},
            "view": None if info is None else {"quality": list(info.quality), "focus": round(info.focus, 1),
                                               "change": None if getattr(self, "last_change", None) is None else
                                               {"kind": self.last_change[0], "age": round(info.wall_at - self.last_change[1], 1)}},
            "gpu": {"ready": bool(self.gpu and self.gpu.ready), "device": self.gpu.device if self.gpu else None, "error": self.gpu_error},
            "scene": {"objects": len(self.scene.objects), "version": self.scene.version},
            # Views held for work in progress and the memory bound they are held in (capture.py).
            "captures": self._captures_wire(),
            "follow": self._follow_wire(),
            "clients": len(self.clients),
            "viewHeld": self.view_held,  # the studio covers the live view to look back at the session (commands.py `view`)
            "machine": self.machine.wire(),
            # Which defaults really ran and how each provider's calls ended, the last ten minutes (fallbacks.py,
            # api_usage.attempt): the meter's panel says so, so a slower run is never taken for the wrong model.
            "models": self._models_wire(),
            # Each GPU worker: started, hung, what is loading there and what it holds (gpu_client.py, loads.py).
            "workers": self._workers_wire(),
            "loopLag": self.loop_lag.wire() if getattr(self, "loop_lag", None) is not None else None,
        }


def _models_health(hub) -> dict:
    from research_worker import api_usage
    from . import fallbacks, models_in_use

    settings = getattr(hub, "settings", None)
    return {"fallbacks": fallbacks.of(hub).recent(), "providers": api_usage.recent(),
            # Who's doing each job now, where it runs and how fast it answered lately (the meter's panel).
            **({"inUse": models_in_use.rows(settings)} if settings is not None else {})}


def _bbox(polygons) -> tuple[float, float, float, float]:
    pts = np.array([p for poly in polygons for p in poly], dtype=float)
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())
