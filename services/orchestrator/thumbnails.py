"""Small pictures for Made today's cards (GET /api/resources/{id}/thumbnail; the studio's madetoday.ts).

Every product (produces.is_product; CONTEXT.md "Product") shows what it is, from a picture the session already
keeps, made smaller; never a new look at the live view:
- cell outlines: their captured view with the cells drawn in their colours, cut to the eyepiece;
- motion tracks: their captured view with the tracks drawn (motiontracks.py's own picture);
- a researcher's work: its newest picture, else its video's first frame, else the view it started from; a picture it
  made or a web picture it cited (ticket 66): that picture; its video: the video's poster frame;
- a reference photo, a bookmark: its picture; footage held or captured: its first frame;
- a thing followed over time: its newest observation's crop, outlined (only cells observed from a population keep one);
- a screen recording: its poster frame.
The user's own recordings and pictures (a Record dataset's camera video, a snapshot, a clearer picture) are sources,
not products (ADR 0022): their small pictures are the library's (`_asset`, GET /api/library/{id}/thumbnail).

Nothing here may take time from the live view (as footage_media.py for the footage library):
- thumbnails are made one at a time on one thread at background priority (efficiency cores and throttled disk
  on macOS), never on the event loop or the frame path; a video's poster frame comes from ffmpeg run as a
  background-priority process;
- they are kept in memory only (at most CACHE_MAX, the least recently used let go), keyed by what the studio
  says the entry is now (`v`), so nothing new is saved; one that cannot be made is asked again after MISS_S.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import ctypes
import io
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

THUMB_PX = 192  # the longest side: Made today shows it at 50-60 CSS px, sharp on a 3x screen
QUALITY = 80
CACHE_MAX = 300  # about 10 KB each
MISS_S = 30.0  # an entry with no picture yet (a screen recording still being written) is asked again after this
POSTER_S = 30.0  # ffmpeg's longest wait for a poster frame


LIBRARY = "library"  # the cache's `day` for a library asset's thumbnail (its ids are the library's, not a session's)
# A researcher's work that is not a product but has a picture (research_entries): a picture it made, a web picture it
# cited, a video's poster frame. Its lessons, tables and charts have none (the kind's icon).
WORK = frozenset({"figure", "source", "video"})


def background_priority() -> None:
    """Lower the calling thread to the lowest priority the system has: QoS background on macOS (efficiency cores,
    throttled CPU and disk, as `taskpolicy -b`), nice 19 for this thread on Linux. Best effort: a system without
    either leaves it as it is."""
    try:
        if sys.platform == "darwin":
            QOS_CLASS_BACKGROUND = 0x09
            libc = ctypes.CDLL("/usr/lib/libSystem.dylib")
            libc.pthread_set_qos_class_self_np(ctypes.c_uint(QOS_CLASS_BACKGROUND), ctypes.c_int(0))
        elif hasattr(os, "setpriority"):
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
    except Exception:  # noqa: BLE001 - a thumbnail at normal priority is still one at a time
        pass


def background(cmd: list[str]) -> list[str]:
    """A command run at the lowest priority (footage_media.background)."""
    if shutil.which("taskpolicy"):
        return ["taskpolicy", "-b", *cmd]
    return ["nice", "-n", "19", *cmd]


def shrink(data: bytes | None, *, region: tuple[float, float, float] | None = None,
           outlines: list[tuple[list, tuple[int, int, int]]] | None = None) -> bytes | None:
    """A picture made small (a JPEG at most THUMB_PX a side): decoded at a reduced scale where the format allows
    (a camera JPEG), cut to `region` (an eyepiece disc: centre and radius, in the picture's px) and with `outlines`
    drawn (polygons in the picture's px, each with its RGB colour). None when it cannot be read."""
    if not data:
        return None
    from PIL import Image, ImageDraw

    try:
        image = Image.open(io.BytesIO(data))
        full_w, full_h = image.size
        crop = None
        if region is not None:
            cx, cy, r = region
            crop = (max(0.0, cx - r), max(0.0, cy - r), min(float(full_w), cx + r), min(float(full_h), cy + r))
            if crop[2] - crop[0] < 8 or crop[3] - crop[1] < 8:
                crop = None
        # Decode only as much as needed: twice the thumbnail across the part kept (outlines drawn there, then
        # reduced, come out smooth).
        span = max(crop[2] - crop[0], crop[3] - crop[1]) if crop else max(full_w, full_h)
        want = min(1.0, 2 * THUMB_PX / max(1.0, span))
        image.draft("RGB", (max(1, int(full_w * want)), max(1, int(full_h * want))))
        image = image.convert("RGB")
        k = image.size[0] / full_w
        if crop:
            box = tuple(int(round(v * k)) for v in crop)
            image = image.crop(box)
            ox, oy = crop[0], crop[1]
        else:
            ox = oy = 0.0
        if outlines:
            draw = ImageDraw.Draw(image)
            width = max(1, round(1.6 * image.size[0] / (THUMB_PX * 1.0)))
            for polygons, colour in outlines:
                for polygon in polygons:
                    points = [((float(p[0]) - ox) * k, (float(p[1]) - oy) * k) for p in polygon]
                    if len(points) >= 2:
                        draw.line(points + points[:1], fill=colour, width=width, joint="curve")
        image.thumbnail((THUMB_PX, THUMB_PX), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, "JPEG", quality=QUALITY, optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - an unreadable picture is no thumbnail, never an error page
        return None


def poster(path: Path | None) -> bytes | None:
    """A video's poster frame, small: a keyframe a second in (the first frame for a shorter one), by ffmpeg at
    background priority. None without ffmpeg or a readable video."""
    if path is None or not Path(path).is_file():
        return None
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    for at in ("1", None):
        cmd = [ffmpeg, "-v", "error", "-nostdin", "-y", *(["-ss", at] if at else []), "-i", str(path),
               "-frames:v", "1", "-vf", f"scale={THUMB_PX}:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "6", "-"]
        try:
            result = subprocess.run(background(cmd), capture_output=True, check=False, timeout=POSTER_S)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.stdout:
            return shrink(result.stdout)
    return None


class Thumbnails:
    """Made today's thumbnails for one index (resources.Index), made off the loop and kept in memory."""

    def __init__(self, index) -> None:
        self.index = index
        self._cache: OrderedDict[tuple, bytes] = OrderedDict()
        self._misses: dict[tuple, float] = {}
        self._pending: dict[tuple, asyncio.Future] = {}
        self._lock = threading.Lock()
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self.made = 0  # how many were made (tests, and the meter if ever asked)

    def _pool(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="thumbnails", initializer=background_priority)
        return self._executor

    def cached(self, eid: str, day: str, v: str = "") -> bytes | None:
        with self._lock:
            data = self._cache.get((day, eid, v))
            if data is not None:
                self._cache.move_to_end((day, eid, v))
            return data

    async def get(self, eid: str, day: str | None = None, v: str = "") -> bytes | None:
        """The entry's thumbnail (a JPEG), or None when it has no picture to make one from. `day`: the session the
        entry belongs to (None: the one open); ids are a session's own, so it is part of the cache's key."""
        day = day or self.index.session()
        key = (day, eid, v)
        data = self.cached(eid, day, v)
        if data is not None:
            return data
        if time.monotonic() - self._misses.get(key, float("-inf")) < MISS_S:
            return None
        pending = self._pending.get(key)
        if pending is None:  # one job per entry and version, however many cards ask at once
            pending = asyncio.get_running_loop().run_in_executor(self._pool(), self.make, eid, day)
            self._pending[key] = pending
            pending.add_done_callback(lambda _: self._pending.pop(key, None))
        data = await asyncio.shield(pending)
        with self._lock:
            if data is None:
                now = time.monotonic()
                if len(self._misses) > CACHE_MAX:  # those asked long ago are let go
                    self._misses = {k: t for k, t in self._misses.items() if now - t < MISS_S}
                self._misses[key] = now
            else:
                self._misses.pop(key, None)
                self._cache[key] = data
                self._cache.move_to_end(key)
                while len(self._cache) > CACHE_MAX:
                    self._cache.popitem(last=False)
        return data

    # --- made on the thumbnails thread --------------------------------------------------------------
    def make(self, eid: str, day: str) -> bytes | None:
        from .produces import is_product

        if day == LIBRARY:
            return self._asset(eid)
        entry = self.index.get(eid, day)
        if entry is None or not (is_product(entry.kind, entry.made_by) or entry.kind in WORK):
            return None
        # Every product kind (produces.PRODUCT_KINDS); a bookmark and a kept result (a reference photo) are their picture.
        # A researcher's picture and a web picture it cited are theirs, its video its poster frame (ticket 66, S4).
        makers: dict[str, Callable[[Any, str], bytes | None]] = {
            "population": self._population, "tracks": self._tracks, "research": self._research, "clip": self._clip,
            "individual": self._individual, "recording": self._recording, "video": self._video}
        try:
            maker = makers.get(entry.kind)
            data = maker(entry, day) if maker is not None else self._picture(entry.id, day)
        except Exception:  # noqa: BLE001 - one store that cannot be read is no thumbnail, never a broken page
            return None
        if data is not None:
            self.made += 1
        return data

    def _asset(self, aid: str) -> bytes | None:
        """A library asset's small picture (library.py; the Sources section): a picture made small, a recording's
        poster frame. Its cache key is (LIBRARY, id, v)."""
        library = getattr(getattr(self.index, "hub", None), "library", None)
        asset = library.get(aid) if library is not None else None
        if asset is None:
            return None
        try:
            data = poster(asset.path) if asset.kind == "recording" else shrink(asset.path.read_bytes())
        except OSError:
            return None
        if data is not None:
            self.made += 1
        return data

    def _picture(self, eid: str, day: str, n: int = 0, region=None, outlines=None) -> bytes | None:
        found = self.index.picture(eid, n, day)
        return shrink(found[0], region=region, outlines=outlines) if found else None

    def _population(self, entry, day: str) -> bytes | None:
        """Its captured view, cut to the eyepiece, with every cell drawn in the colour the layer gives it."""
        from .evidence import evidence_of
        from .population import member_bgr

        population = evidence_of(self.index.hub).get(entry.id) if self.index.is_open(day) else None
        outlines, region = [], None
        if population is not None:
            disc = (population.view or {}).get("disc")
            region = tuple(disc) if disc and len(disc) == 3 else None
            for member in list(population.live()):
                b, g, r = member_bgr(self.index.hub, population, member.id)
                outlines.append((list(member.polygons or []), (int(r), int(g), int(b))))
        return self._picture(entry.id, day, region=region, outlines=outlines)

    def _tracks(self, entry, day: str) -> bytes | None:
        from .motiontracks import tracks_of

        item = tracks_of(self.index.hub).get(entry.id) if self.index.is_open(day) else None
        disc = (getattr(item, "view", None) or {}).get("disc")
        return self._picture(entry.id, day, region=tuple(disc) if disc and len(disc) == 3 else None)

    def _research(self, entry, day: str) -> bytes | None:
        if entry.pictures:
            return self._picture(entry.id, day)
        from .resources import _sitting

        research = self.index.research
        session = research.get(entry.id) if research is not None else None
        if session is None or _sitting(session) != day:  # another session's with this id: that session's own
            session = next((s for s in self.index._researchers(day) if s.rid == entry.id), None)
        if session is None:
            return None
        from .research.files import research_file

        root = Path(session.root)
        videos = [o for o in session.current_outputs() if o.get("kind") == "video" and o.get("path")]
        for output in videos:
            sub, _, name = str(output["path"]).partition("/")
            found = research_file(root, session.day, session.rid, sub, name)
            if found is not None:
                frame = poster(found[0])
                if frame is not None:
                    return frame
        for name in (("crop.jpg", "frame.jpg") if getattr(session, "has_crop", False) else ("frame.jpg",)):
            found = research_file(root, session.day, session.rid, "inputs", name)
            if found is not None:
                return shrink(found[0].read_bytes())
        return None

    def _video(self, entry, day: str) -> bytes | None:
        from . import research_entries

        session, part = self.index.work_session(entry.id, day)
        if session is None:
            return None
        root = getattr(session, "root", None) or getattr(self.index.research, "root", None)
        found = research_entries.video_file(session, part, Path(root) if root is not None else None)
        return poster(found[0]) if found is not None else None

    def _clip(self, entry, day: str) -> bytes | None:
        return self._picture(entry.id, day, 0)

    def _individual(self, entry, day: str) -> bytes | None:
        """Its newest observation that kept a picture: a cell of a population, cropped and outlined on its view."""
        identity = self.index.identity
        if identity is None or not self.index.is_open(day):
            return None
        from .identity import is_member
        from .population import captured_picture

        for observation in reversed(identity.history(entry.id)):
            node = observation.get("node") or ""
            if not is_member(node):
                continue
            population, member = identity.member(node)
            if population is None or member is None:
                continue
            data = captured_picture(self.index.hub, population, [member.id], outlines=True)
            if data:
                return shrink(data)
        return None

    def _recording(self, entry, day: str) -> bytes | None:
        root = self.index.recordings
        if root is None:
            return None
        files = sorted(p for p in root.glob(f"{entry.id}*") if p.suffix in (".mp4", ".webm") and p.is_file())
        playable = [p for p in files if p.stem.endswith("-play")]  # the copy QuickTime plays seeks best
        return poster((playable or files or [None])[0])

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)


def thumbnails_of(hub) -> Thumbnails:
    """The hub's thumbnails (made on first use, over its index)."""
    found = getattr(hub, "thumbnails", None)
    if isinstance(found, Thumbnails):
        return found
    from .resources import resources_of

    made = Thumbnails(resources_of(hub))
    try:
        hub.thumbnails = made
    except AttributeError:
        pass
    return made
