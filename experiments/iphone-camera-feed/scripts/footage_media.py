"""The test footage library, its thumbnails and uploads, for the camera bridge (assets/README.md).

Recordings and pictures: a picture plays as one held frame (simulated_camera.py), so it is listed, added and
shown the same way, as `kind` "still" (a recording is "recording").

The bridge is the camera path: every frame of the live view goes through it. So nothing here may take
time from it:
- the library is scanned at most every LIBRARY_TTL_S and served from memory (`version` changes only
  when a file is added, removed or changed), never inside the 3 s state poll;
- lengths and thumbnails are made by one worker, and each ffprobe/ffmpeg runs as a background-priority
  process (`taskpolicy -b` on macOS: efficiency cores, throttled CPU and disk; `nice -n 19` elsewhere);
  thumbnails are cached on disk and asked for only when they show. The worker follows the machine
  (`--media-pace follow`, the default since L1c): up to two speculative jobs while the bridge keeps its
  frame rate, with full capacity for requested thumbnails; one while starting/slow, and MEDIA_JOBS_MAX without capture (CameraBridge.media_pace);
  `--media-pace one` is the earlier way, one file at a time;
- uploads stream to disk in 1 MB pieces (never held in memory), hashed as they arrive; as an option
  (`--upload-priority background`) the copy runs on a thread of its own at background priority
  (`in_background`), as the thumbnails do.

Standard library only: the bridge runs on the system Python.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from simulated_camera import STILL_SUFFIXES, VIDEO_SUFFIXES

LIBRARY_TTL_S = 10.0  # the folder is scanned again at most this often (or at once after an upload)
THUMB_WIDTH = 240  # px; shown at 56 px in the menu and up to ~220 px in the Assets grid
THUMB_WAIT_S = 20.0  # a request waits this long for its thumbnail before giving up (the studio retries)
UPLOAD_MAX_BYTES = 8 * 1024 ** 3
CHUNK = 1 << 20
ADDED = "added"  # uploads land in footage/added/, with a .json beside each, until `make assets-adopt`
GROUP_ORDER = {"Real microscope": 0, "Added here": 1, "Camera, not on the microscope": 2, "Other": 4}
MEDIA_JOBS_MAX = 8  # media jobs at once on a quiet Mac, when the worker's pace follows the machine
PACE_RECHECK_S = 0.25  # a job held back by the pace is looked at again this often (the pace follows the frame rate)


def background(cmd: List[str]) -> List[str]:
    """Run a command at the lowest priority the system has, so the live feed never waits for it."""
    if shutil.which("taskpolicy"):
        return ["taskpolicy", "-b", *cmd]
    return ["nice", "-n", "19", *cmd]


QOS_CLASS_BACKGROUND = 0x09  # macOS, <sys/qos.h>


def _lower_this_thread() -> None:
    """This thread at background priority, as `taskpolicy -b` puts a process: efficiency cores, and its disk reads and
    writes throttled (the class does both; the older setiopolicy_np would take the thread out of its class again).
    One way only: a thread lowered so cannot raise itself again, so only a thread made for the work is lowered.
    Nothing elsewhere than macOS."""
    if sys.platform != "darwin":
        return
    try:
        system = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        system.pthread_set_qos_class_self_np(ctypes.c_uint(QOS_CLASS_BACKGROUND), ctypes.c_int(0))
    except (OSError, AttributeError):
        pass


def in_background(fn: Callable[[], Any]) -> Any:
    """`fn()` on a thread of its own at background priority, waited for here: its answer, or its exception raised
    here. For work on a request thread of the bridge, which also serves the live frames (an upload's copy)."""
    outcome: Dict[str, Any] = {}

    def run() -> None:
        _lower_this_thread()
        try:
            outcome["value"] = fn()
        except BaseException as exc:  # handed back to the caller, whatever it is
            outcome["error"] = exc

    thread = threading.Thread(target=run, name="footage-background", daemon=True)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def probe_seconds(path: Path) -> float:
    result = subprocess.run(
        background([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=nw=1:nk=1", str(path)]),
        capture_output=True, text=True, check=False,
    )
    try:
        return round(float(result.stdout.strip()), 1)
    except ValueError:
        return 0.0


def probe_picture(path: Path) -> Optional[Tuple[int, int]]:
    """A picture's width and height, or None when the file does not open as one."""
    result = subprocess.run(
        background([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)]),
        capture_output=True, text=True, check=False,
    )
    try:
        width, height = (int(n) for n in result.stdout.strip().splitlines()[0].split(",")[:2])
    except (ValueError, IndexError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def kind_of(path: Path) -> str:
    """"still" for a picture, "recording" for a video: how the library and the studio tell them apart."""
    return "still" if path.suffix.lower() in STILL_SUFFIXES else "recording"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "recording"


def span(secs: float) -> str:
    whole = int(round(secs))
    return f"{whole // 60}m{whole % 60:02d}s" if whole >= 60 else f"{whole}s"


class Worker:
    """One slow job at a time. Waiting requests run in arrival order ahead of prefetch;
    prefetch stays newest first. New arrivals must not starve requests already waiting.

    With a `pace` (the option `--media-pace follow`), `pace(requested)` says how many jobs may run at once now,
    for the next job (requested or prefetch), up to MEDIA_JOBS_MAX: the worker then follows the machine."""

    def __init__(self, pace: Optional[Callable[[bool], int]] = None) -> None:
        self._jobs: List[tuple] = []  # (key, fn, done event, requested)
        self._keys: Dict[str, threading.Event] = {}
        self._lock = threading.Condition()
        self.done = 0
        self.running = 0
        self.pace = pace
        for n in range(MEDIA_JOBS_MAX if pace else 1):
            threading.Thread(target=self._run, name=f"footage-media-{n}", daemon=True).start()

    def submit(self, key: str, fn: Callable[[], None], *, requested: bool = False) -> threading.Event:
        with self._lock:
            if key in self._keys:
                event = self._keys[key]
                for i, job in enumerate(self._jobs):
                    if job[0] == key and not job[3]:
                        # Promote prefetch, retaining its work/event. An existing request keeps
                        # its place even if another client asks for the same thumbnail again.
                        self._jobs.append((*self._jobs.pop(i)[:3], requested))
                        self._lock.notify_all()
                        break
                return event
            event = threading.Event()
            self._keys[key] = event
            self._jobs.append((key, fn, event, requested))
            self._lock.notify_all()
            return event

    def pending(self) -> int:
        with self._lock:
            return len(self._jobs)

    def _allowed(self, requested: bool) -> int:
        if self.pace is None:
            return 1
        try:
            return min(MEDIA_JOBS_MAX, max(0, int(self.pace(requested))))
        except Exception:  # a pace that fails is today's way: one at a time
            return 1

    def _run(self) -> None:
        while True:
            with self._lock:
                while True:
                    if not self._jobs:
                        self._lock.wait()
                        continue
                    first_request = next((i for i, job in enumerate(self._jobs) if job[3]), len(self._jobs) - 1)
                    if self.running < self._allowed(self._jobs[first_request][3]):
                        break
                    # Held back by the pace, which changes with the frame rate: looked at again.
                    self._lock.wait(timeout=PACE_RECHECK_S if self.pace else None)
                key, fn, event, _ = self._jobs.pop(first_request)
                self.running += 1
            try:
                fn()
            except Exception:  # a broken file never stops the worker
                pass
            finally:
                with self._lock:
                    self.running -= 1
                    self._keys.pop(key, None)
                    self.done += 1
                    self._lock.notify_all()
                event.set()


class Library:
    """The footage folder as the camera menu and the Assets view show it, cached."""

    def __init__(self, folder: Path, manifest: Path, corpus: Path, worker: Optional[Worker] = None) -> None:
        self.folder, self.manifest, self.corpus = folder, manifest, corpus
        self.worker = worker or Worker()
        self._items: List[dict] = []
        self._version = ""
        self._scanned_at: Optional[float] = None  # never yet (time.monotonic() can start near 0)
        self._lock = threading.Lock()
        self._lengths: Dict[str, list] = {}  # absolute path -> [mtime, size, seconds]
        self._lengths_loaded = False
        self._saving = threading.Lock()  # lengths are measured side by side when the worker follows the machine
        self._prewarmed = False
        self._corpus_optics: Dict[str, dict] = {}

    # --- the list ---------------------------------------------------------------------------------
    def items(self) -> List[dict]:
        with self._lock:
            if self._scanned_at is None or time.monotonic() - self._scanned_at > LIBRARY_TTL_S:
                self._items = self._scan()
                self._version = hashlib.sha1(json.dumps(
                    [(i["name"], i["bytes"], i["mtime"], i["seconds"], i["title"]) for i in self._items]).encode()).hexdigest()[:16]
                self._scanned_at = time.monotonic()
            return self._items

    def version(self) -> str:
        self.items()
        return self._version

    def invalidate(self) -> None:
        with self._lock:
            self._scanned_at = None

    def find(self, name: str) -> Optional[dict]:
        for item in self.items():
            if item["name"] == name:
                return item
        return None

    def path(self, item: dict) -> Path:
        return self.folder / item["name"]

    def _catalogue(self):
        real, corpus = {}, {}
        try:
            for item in json.loads(self.manifest.read_text()).get("real", []):
                real[str(Path(item["file"]).relative_to("footage"))] = item
        except (OSError, ValueError, KeyError):
            pass
        self._corpus_optics = {}
        try:
            for item in json.loads(self.corpus.read_text()).get("items", []):
                corpus[item["id"]] = item.get("specimen", item["id"])
                if item.get("optics"):
                    self._corpus_optics[item["id"]] = item["optics"]
        except (OSError, ValueError, KeyError):
            pass
        return real, corpus

    @staticmethod
    def corpus_id(name: str, corpus: Dict[str, str]) -> Optional[str]:
        rest = Path(name).stem[len("corpus-"):] if Path(name).stem.startswith("corpus-") else None
        if rest is None:
            return None
        for cid in sorted(corpus, key=len, reverse=True):
            if rest == cid or (rest.startswith(cid + "-") and rest[len(cid) + 1:].isdigit()):
                return cid
        return None

    @staticmethod
    def corpus_entry(name: str, corpus: Dict[str, str]):
        stem = Path(name).stem
        if not stem.startswith("corpus-"):
            return None
        rest = stem[len("corpus-"):]
        for cid in sorted(corpus, key=len, reverse=True):  # the longest id that fits ("pond-seah" before "pond")
            if rest == cid or (rest.startswith(cid + "-") and rest[len(cid) + 1:].isdigit()):
                n = rest[len(cid) + 1:]
                return f"Corpus: {corpus[cid]}", f"{corpus[cid][:1].upper()}{corpus[cid][1:]}" + (f" ({int(n) + 1})" if n else "")
        return None

    def _scan(self) -> List[dict]:
        if not self.folder.is_dir():
            return []
        self._load_lengths()
        real, corpus = self._catalogue()
        items, unknown = [], []
        for path in self.folder.rglob("*"):
            rel = path.relative_to(self.folder)
            if (path.suffix.lower() not in VIDEO_SUFFIXES | STILL_SUFFIXES or any(part.startswith(".") for part in rel.parts)
                    or not path.is_file()):
                continue
            name, stat = rel.as_posix(), path.stat()
            known = self._lengths.get(str(path))
            seconds = known[2] if known and known[0] == stat.st_mtime and known[1] == stat.st_size else None
            extra = {}
            if name in real:
                entry = real[name]
                group = "Real microscope" if entry.get("microscope", True) else "Camera, not on the microscope"
                title, seconds = entry.get("title", name), entry.get("seconds", seconds)
                extra = {"specimen": entry.get("specimen", ""), "date": entry.get("date", ""), "backedUp": True,
                         "optics": entry.get("optics")}
            elif rel.parts[0] == ADDED:
                side = self._sidecar(path)
                group, title = "Added here", side.get("title") or path.name
                seconds = side.get("seconds", seconds)
                extra = {"specimen": side.get("specimen", ""), "date": side.get("date", ""), "backedUp": False,
                         "microscope": side.get("microscope", True), "optics": side.get("optics")}
            else:
                entry = self.corpus_entry(name, corpus)
                group, title = entry if entry else ("Other", path.name)
                cid = self.corpus_id(name, corpus)
                if cid and cid in self._corpus_optics:
                    extra = {"optics": self._corpus_optics[cid]}
            kind = kind_of(path)
            if kind == "still":
                seconds = 0.0  # a picture has no length, and nothing to measure
            if seconds is None:
                unknown.append(path)
            items.append({"name": name, "title": title, "group": group, "kind": kind, "seconds": seconds,
                          "truth": path.with_suffix(".truth.json").exists(), "bytes": stat.st_size,
                          "mtime": int(stat.st_mtime), **extra})
        for path in unknown:
            self.worker.submit(f"length:{path}", lambda p=path: self._measure(p))
        self._prewarm(items)
        items.sort(key=lambda i: (GROUP_ORDER.get(i["group"], 3), i["group"].lower(), i["name"].lower()))
        return items

    @staticmethod
    def _sidecar(path: Path) -> dict:
        try:
            return json.loads(path.with_suffix(path.suffix + ".json").read_text())
        except (OSError, ValueError):
            return {}

    # --- lengths, measured in the background and kept beside the footage -------------------------
    def _load_lengths(self) -> None:
        if self._lengths_loaded:
            return
        self._lengths_loaded = True
        try:
            saved = json.loads((self.folder / ".durations.json").read_text())
            for name, value in saved.items():
                self._lengths.setdefault(str(self.folder / name), value)
        except (OSError, ValueError):
            pass

    def _measure(self, path: Path) -> None:
        stat = path.stat()
        self._lengths[str(path)] = [stat.st_mtime, stat.st_size, probe_seconds(path)]
        if self.worker.pending() == 0 or len(self._lengths) % 50 == 0:  # saved now and then, not per file
            self._save_lengths()
            self.invalidate()

    def _save_lengths(self) -> None:
        known = {}
        for key, value in list(self._lengths.items()):
            try:
                known[str(Path(key).relative_to(self.folder))] = value
            except ValueError:
                continue
        try:
            with self._saving:
                tmp = self.folder / ".durations.json.tmp"
                tmp.write_text(json.dumps(known))
                tmp.replace(self.folder / ".durations.json")
        except OSError:
            pass

    # --- thumbnails -------------------------------------------------------------------------------------
    def _prewarm(self, items: List[dict]) -> None:
        """Every thumbnail made ahead, once, at background priority (about 0.4 s each), so the menu and
        the Assets view show pictures at once. The real recordings go last into the line, so they come
        out first; anything asked for on screen jumps ahead of all of it."""
        if self._prewarmed:
            return
        self._prewarmed = True
        for item in sorted(items, key=lambda i: GROUP_ORDER.get(i["group"], 3), reverse=True):
            out = self.thumb_path(item)
            if not out.exists():
                self.worker.submit(f"thumb:{out}", lambda i=item, o=out: self._make_thumb(i, o))


    def thumb_path(self, item: dict) -> Path:
        key = hashlib.sha1(f"{item['name']}|{item['bytes']}|{item['mtime']}".encode()).hexdigest()[:20]
        return self.folder / ".thumbs" / f"{key}.jpg"

    def thumbnail(self, item: dict, wait_s: float = THUMB_WAIT_S) -> Optional[bytes]:
        """The item's thumbnail (a JPEG), made in the background worker if it is not cached yet."""
        out = self.thumb_path(item)
        if not out.exists():
            self.worker.submit(f"thumb:{out}", lambda: self._make_thumb(item, out), requested=True).wait(wait_s)
        try:
            return out.read_bytes()
        except OSError:
            return None

    def _make_thumb(self, item: dict, out: Path) -> None:
        if out.exists():
            return
        out.parent.mkdir(parents=True, exist_ok=True)
        if item.get("kind") == "still":
            # A picture is its own first frame: scaled down, nothing to seek past.
            tmp = out.with_suffix(".tmp.jpg")
            cmd = [shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-threads", "1", "-filter_threads", "1",
                   "-i", str(self.path(item)),
                   "-frames:v", "1", "-vf", f"scale={THUMB_WIDTH}:-2", "-q:v", "6", "-threads", "1", str(tmp)]
            subprocess.run(background(cmd), capture_output=True, check=False, timeout=60)
            if tmp.exists() and tmp.stat().st_size > 0:
                tmp.replace(out)
            return
        seconds = item.get("seconds") or 0
        at = min(max(seconds * 0.3, 0.5), 60.0) if seconds else 1.0  # a third in: past any fade-in
        tmp = out.with_suffix(".tmp.jpg")
        # Keyframes only: decoding stops at the first one after `at` (a quarter faster, and a thumbnail
        # needs no exact frame).
        # One queued process can still start dozens of codec/filter threads. Bound
        # all three pools so a tiny thumbnail does not compete with the live feed.
        cmd = [shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-threads", "1", "-filter_threads", "1",
               "-skip_frame", "nokey", "-ss", f"{at:.2f}", "-i", str(self.path(item)),
               "-frames:v", "1", "-vf", f"scale={THUMB_WIDTH}:-2", "-q:v", "6", "-threads", "1", str(tmp)]
        subprocess.run(background(cmd), capture_output=True, check=False, timeout=60)
        if not tmp.exists() or tmp.stat().st_size == 0:  # shorter than `at`: the first frame
            cmd[cmd.index("-ss") + 1] = "0"
            subprocess.run(background(cmd), capture_output=True, check=False, timeout=60)
        if tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(out)

    # --- uploads ------------------------------------------------------------------------------------------
    def receive(self, stream, length: int, filename: str, title: str, specimen: str, microscope: bool,
                optics: Optional[dict] = None) -> dict:
        """A recording or a picture sent from the studio: streamed to footage/added/ with a .json beside it (title,
        specimen, kind, checksum...), listed under "Added here" at once. `make assets-adopt` moves it into
        footage/real/ and the manifest; `make assets-backup` then backs it up."""
        suffix = Path(filename).suffix.lower()
        still = suffix in STILL_SUFFIXES
        if suffix not in VIDEO_SUFFIXES and not still:
            raise ValueError(f"Only videos ({', '.join(sorted(VIDEO_SUFFIXES))}) and pictures "
                             f"({', '.join(sorted(STILL_SUFFIXES))}) can be added")
        if length <= 0 or length > UPLOAD_MAX_BYTES:
            raise ValueError("The file is empty or larger than 8 GB")
        added = self.folder / ADDED
        added.mkdir(parents=True, exist_ok=True)
        tmp = added / f".upload-{os.getpid()}-{threading.get_ident()}{suffix}"
        digest, left = hashlib.sha256(), length
        try:
            with tmp.open("wb") as f:
                while left > 0:
                    block = stream.read(min(CHUNK, left))
                    if not block:
                        raise ValueError("The upload stopped before the end of the file")
                    digest.update(block)
                    f.write(block)
                    left -= len(block)
            # A picture has no length: it must open as a picture instead (its size is kept with it).
            size = probe_picture(tmp) if still else None
            if still and size is None:
                raise ValueError("That file does not open as a picture")
            seconds = 0.0 if still else probe_seconds(tmp)
            if not still and seconds <= 0:
                raise ValueError("That file does not play as a video")
            date = time.strftime("%Y-%m-%d")
            length_words = "picture" if still else span(seconds)
            base = f"{date}-{slug(specimen or Path(filename).stem)}-{length_words}"
            dest, n = added / f"{base}{suffix}", 2
            while dest.exists():
                dest, n = added / f"{base}-{n}{suffix}", n + 1
            tmp.replace(dest)
        finally:
            if tmp.exists():
                tmp.unlink()
        side = {"title": title or f"{(specimen or Path(filename).stem)[:1].upper()}{(specimen or Path(filename).stem)[1:]}, {date} ({length_words})",
                "specimen": specimen, "date": date, "microscope": microscope, "kind": "still" if still else "recording",
                "seconds": seconds, **({"width": size[0], "height": size[1]} if size else {}), "bytes": length,
                "sha256": digest.hexdigest(), "originalName": filename, "addedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "optics": {k: v for k, v in (optics or {}).items() if v not in (None, "")}}
        dest.with_suffix(dest.suffix + ".json").write_text(json.dumps(side, indent=1))
        self.invalidate()
        return self.find(dest.relative_to(self.folder).as_posix()) or {"name": dest.name, **side}
