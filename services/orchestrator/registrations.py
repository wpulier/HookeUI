"""Volumes the user adds and the registrations run on them (phase 3's E5; the review's corrections 5 and 6), kept under
paths.jobs per date: `v3/` a volume, `j2/` a job.

- **A volume** (`v3`) is a whole-brain image stack the user added (a TIFF or OME-TIFF on this Mac): its file kept as it
  was (cloned), what its file states read by brainreg's own Python (jobs.run, `adapter:inspect`: its series, axes, shape
  and type, and voxel sizes where the file states them), a preview of its middle slice, and what the user said of it.
  Every fact carries its source: `file` (stated by the file), `user` (said by the user, with when) or `fixture`.
  - **Axes:** the three spatial axes in array order (z, y, x); a channel or a time point is chosen when the file holds
    several (the job's adapter takes that one 3D stack).
  - **Voxel sizes** (µm, z y x): from the file where it states them, else the user's.
  - **Orientation:** BrainGlobe's three letters for the array's axes (`psl`: posterior, superior, left at index 0), always
    the user's or a fixture's receipt, never read from the file's axis labels.
- **A job** (`j2`) is one registration of one volume: its record says exactly what it ran on and with (the volume and
  its file's sha256, the facts and their sources, the parameters, the environment's generation and lockfile hash, its
  native recipe, the atlas and its revision, brainreg's version) and, once done, every output by its sha256, the region
  volumes measured and a picture in brainreg's own output space (its downsampled volume with the registered atlas's
  boundaries, never over the original slices).
  - **States:** queued, running, done, failed (with the tool's words), cancelled (its partial outputs kept, marked, never
    a result) and interrupted (running when the app stopped: offered to run again with the same record, never resumed by
    itself).
  - **Reopening** a done job reads its outputs; nothing is computed again.
- **A new kind of job on a volume** (kinds.py, C6: an entry whose `[[implements]]` takes `volume.3d` and gives a volume
  or a table) runs on the same volumes the same way (`start_kind`): its record names the entry, its operation and output,
  its environment generation and lockfile, the volume and the facts it ran with (no orientation is asked: only a
  registration needs one); once done, what it gave is read back by the app by its sha256 and checked against what it
  declared (jobs.accept_kind, inside its run's own deadline and cancel): a volume's shape and type, with a picture of its middle plane drawn by the app, or a
  table's columns and rows (`table`, read by tables.table_of). A volume can be added with no brainreg installed when such
  a kind is: its file's facts are then read by the app's own Python (volume_inspect.py), in a process of its own.
- **Filed** in the session's journal as made (a job is a product: Made in this session), listed in the session's index
  (kinds "volume" and "job"), each record written atomically and last.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from gpu_worker import manifests as catalog
from gpu_worker import model_files

from . import envs, job_outputs, jobs
from .project_registration_files import RegistrationFiles
from .project_roots import ProjectLocationError
from .scientific_projects import InvalidProject

ENTRY = "brainreg"  # the job entry a volume is registered with (catalog/models/brainreg)
ATLAS = "allen-mouse-25um"
LETTERS = {"a": "anterior", "p": "posterior", "s": "superior", "i": "inferior", "l": "left", "r": "right"}
PAIRS = ("ap", "si", "lr")
STATES = ("queued", "running", "done", "failed", "cancelled", "interrupted")
REGIONS_SAID = 8  # the largest regions a job's summary names
INSPECT_S = 120.0  # the app's own reading of a volume's file (volume_inspect.py), at most


class CantDo(RuntimeError):
    """What could not be done, in plain words, with what to do next."""


def orientation_problem(letters: str) -> str:
    """"" when `letters` are BrainGlobe's three letters (one of each pair: a/p, s/i, l/r), else what is wrong."""
    letters = (letters or "").strip().lower()
    if len(letters) != 3 or any(c not in LETTERS for c in letters):
        return "an orientation is three letters, each one of a, p, s, i, l, r (psl: posterior, superior, left)"
    if sorted(next(p for p in PAIRS if c in p) for c in letters) != sorted(PAIRS):
        return "an orientation names each direction once: one of a/p, one of s/i, one of l/r"
    return ""


def orientation_words(letters: str) -> str:
    return ", ".join(f"axis {n} towards {LETTERS[c]}" for n, c in enumerate(letters.lower()))


def _atomic(path: Path, data: bytes, check: Callable[[], None] = lambda: None) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    check()  # slow preparation still belongs to the original job allowance
    tmp.replace(path)
    check()  # a slow rename is not permission to accept an expired result


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def _outputs(result: dict, subfolder: str) -> dict:
    """Retain only the parent-computed identities, including pictures returned outside the outputs mapping."""
    names = dict(result.get("outputs") or {})
    names.update({k: result[k] for k in ("picture", "preview") if result.get(k) is not None})
    return {key: {"path": f"{subfolder}/{name}", **result["outputFiles"][name]} for key, name in names.items()}


def _choose_series(record: dict, n: int, how: dict) -> None:
    """Series `n` is the one registered: its own axes and the voxel sizes its file states for it (or none: asked); the
    channel and time point chosen for another series are cleared (asked again when this one holds several), and so is
    the orientation when its axes differ (finding 2 of the review of 2026-10-13: a series' scale never becomes
    another's). What it cleared is kept on the series fact."""
    facts, chosen = record["facts"], record["series"][n]
    before = facts["series"]["value"]
    old = record["series"][before] if before is not None else None
    cleared = {k: facts[k] for k in ("voxel_um", "channel", "time", "axes", "orientation")
               if before is not None and facts[k].get("value") is not None}
    facts["series"] = {"value": n, **how, **({"cleared": cleared} if cleared else {})}
    facts["axes"] = {"value": chosen.get("axes"), "source": "file"}
    voxel = chosen.get("voxel_um")
    facts["voxel_um"] = {"value": voxel, "source": "file"} if voxel else {"value": None, "source": None}
    facts["channel"] = {"value": None, "source": None}
    facts["time"] = {"value": None, "source": None}
    if old is not None and old.get("spatial") != chosen.get("spatial"):
        facts["orientation"] = {"value": None, "source": None}


INTERRUPTED = "the app stopped while it ran: run it again (the same record)"


class Registrations:
    def __init__(self, root: Path | None, store=None, today: Callable[[], str] | None = None,
                 said: Callable[[dict], None] = lambda record: None) -> None:
        self.root = Path(root) if root is not None else None
        self.store = store  # the session store (day/store.py)
        self.files = RegistrationFiles(self.root, store) if self.root is not None else None
        self.today = today or (lambda: time.strftime("%Y-%m-%d"))
        self.said = said  # told of every change of a job's state (App: Activity, the studio), from any thread
        self.post: Callable[[Callable[[], None]], None] = lambda fn: fn()  # to the event loop (App.start sets it)
        self._lock = threading.Lock()
        self._given: dict[str, int] = {"v": 0, "j": 0}
        self._stop: dict[tuple[str, str], bool] = {}
        self._threads: dict[tuple[str, str], threading.Thread] = {}
        self._stopping = False  # the app is stopping: a job it ends is interrupted, not cancelled
        # Readers see the prior state until publication is accepted. Rejected terminal metadata stays for this
        # process: a concurrent reader may already hold provisional done bytes even after disk correction finishes.
        # No image/volume bytes are retained. A failure that cannot be saved explicitly says so in its record.
        self._settling: dict[tuple[str, str, str], dict] = {}

    # --- where things are -------------------------------------------------------------------------------------------
    def running(self) -> bool:
        """A job still holds a runtime or atlas, including a job from another session."""
        with self._lock:
            return any(thread.is_alive() for thread in self._threads.values())

    def session(self) -> str:
        return getattr(self.store, "sid", None) or self.today()

    def _new_id(self, prefix: str, session: str) -> tuple[str, Path]:
        from .day.ids import next_number, owner

        folder = self.files.date_folder(session, self.today(), write=True)
        used = [int(m.group(1)) for p in folder.glob(f"{prefix}*") if (m := re.fullmatch(prefix + r"(\d+)", p.name))]
        self._given[prefix] = next_number(owner(self.store, session), prefix, max([self._given[prefix], *used]), folder.name)
        rid = f"{prefix}{self._given[prefix]}"
        d = folder / rid
        d.mkdir()
        self.files.folder({'session': session, 'date': folder.name, 'id': rid})
        return rid, d

    def records(self, prefix: str, session: str | None = None, dates=None) -> list[dict]:
        """Every record of kind `prefix` ("v" or "j") of `session` (default: the one open), from its dates."""
        if self.root is None:
            return []
        session = session or self.session()
        root = self.files.root_for(session)
        dates = list(dates) if dates is not None else (sorted(p.name for p in root.iterdir() if p.is_dir() and re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.name))
                                                        if root.is_dir() else [])
        out = []
        for date in dates:
            folder = self.files.date_folder(session, date)
            for d in sorted(folder.glob(f"{prefix}*")) if folder.is_dir() else ():
                try:
                    record = self.files.read(session, date, d.name)
                except (OSError, ValueError):
                    continue
                if record.get("session") == session:
                    out.append(record)
        visible = {(r["session"], r["date"], r["id"]): r for r in out}
        for key, record in self._settling.copy().items():
            if record.get("session") == session and record["id"].startswith(prefix) and key[1] in dates:
                visible[key] = copy.deepcopy(record)
        return list(visible.values())

    def get(self, rid: str, session: str | None = None) -> dict | None:
        return next((r for r in self.records(rid[0], session) if r.get("id") == rid), None)

    def folder(self, record: dict) -> Path:
        return self.files.folder(record)

    def _write(self, record: dict, check: Callable[[], None] = lambda: None) -> dict:
        self.files.write(record, check, _atomic)
        return record

    def _journal(self, kind: str, record: dict) -> None:
        made = getattr(self.store, "made", None)
        if made is None:
            return

        def file() -> None:  # on the event loop, where the session's state lives
            try:
                made(kind, record["id"], day=record["date"], thread=record.get("thread"), session=record.get("session"))
            except Exception:
                pass  # the record is kept whatever happens to its line in the journal

        self.post(file)

    # --- volumes ----------------------------------------------------------------------------------------------------
    def entry(self) -> catalog.Manifest:
        m = catalog.get(ENTRY)
        if m is None:
            raise CantDo("brainreg is not in this catalog")
        from . import env_runner

        if env_runner.in_use(m) is None:
            raise CantDo(f"brainreg isn't installed here yet ({env_runner.ready(m)}): its Install on the Models page reads "
                         "and registers volumes")
        return m

    def volume_kinds(self) -> list:
        """The new kinds of job on volumes installed and proven here (kinds.py), their manifests."""
        from . import env_runner

        out = []
        for m in env_runner.entries():
            if jobs.volume_kind(m) is not None and env_runner.in_use(m) is not None:
                out.append(m)
        return out

    def _reader(self):
        """brainreg's own inspect when it is installed, else the app's (a volume kind is on), else CantDo."""
        try:
            return self.entry()
        except CantDo as exc:
            if self.volume_kinds():
                return None
            raise CantDo(f"{exc}; or turn on a capability that works on volumes") from None

    def _inspect_here(self, kept: Path, out: Path) -> dict:
        import sys

        script = Path(__file__).resolve().parent / "volume_inspect.py"
        from . import volume_inspect

        try:
            with jobs.MEMORY.reserve(volume_inspect.PEAK_MB):  # its peak, in the same admission as jobs
                done = subprocess.run([sys.executable, "-I", str(script), str(kept), str(out)], capture_output=True,
                                      timeout=INSPECT_S, text=True)
        except subprocess.TimeoutExpired as exc:
            raise CantDo(f"reading {kept.name} took longer than {INSPECT_S:.0f} s") from exc
        except jobs.Failed as exc:
            raise CantDo(str(exc)) from exc
        try:
            seen = json.loads(done.stdout.strip().splitlines()[-1]) if done.returncode == 0 else None
        except (ValueError, IndexError):
            seen = None
        if not isinstance(seen, dict):
            last = (done.stderr.strip().splitlines() or ["no words"])[-1][-300:]
            raise CantDo(f"{kept.name} couldn't be read as a volume: {last}")
        with job_outputs.Folder(out) as folder:
            names = {k: v for k, v in (seen.get("outputs") or {}).items() if isinstance(v, str)}
            seen["outputs"] = names
            seen["outputFiles"] = {v: folder.identity(v) for v in names.values()}
        return seen

    def add_volume(self, path: str, *, thread: str | None = None, session: str | None = None,
                   name: str = "") -> dict:
        """Keep the file at `path` as a volume of the session and read what it states (see the module's docstring).
        Off the event loop: it copies and inspects."""
        if self.root is None:
            raise CantDo("there is no folder for volumes in this run")
        src = Path(path).expanduser()
        if not src.is_file() or src.suffix.lower() not in (".tif", ".tiff"):
            raise CantDo("a volume is a TIFF or OME-TIFF file on this Mac (.tif or .tiff)")
        m = self._reader()
        session = session or self.session()
        with self._lock:
            vid, d = self._new_id("v", session)
        kept = d / re.sub(r"[^\w.\- ]", "_", src.name)[:120]
        if subprocess.run(["cp", "-c", str(src), str(kept)], capture_output=True).returncode:
            shutil.copyfile(src, kept)  # a clone where the disk allows it, a copy elsewhere
        if m is None:
            seen = self._inspect_here(kept, d / "inspect")
        else:
            try:
                seen = jobs.run(m, {"input": str(kept)}, d / "inspect", function="inspect")
            except jobs.Failed as exc:
                raise CantDo(f"brainreg couldn't read {src.name}: {exc}") from exc
        series = seen.get("series") or []
        if not series:
            raise CantDo(f"{src.name} holds no image stack")
        if len(series) == 1 and series[0].get("voxel_um") is None and seen.get("voxel_um"):
            series[0]["voxel_um"] = seen["voxel_um"]  # an adapter that states the file's scale, not each series'
        record = {"id": vid, "kind": "volume", "session": session, "date": d.parent.name, "thread": thread,
                  "at": round(time.time(), 3), "name": name or src.stem, "file": kept.name, "from": str(src),
                  "bytes": kept.stat().st_size, "sha256": _sha256(kept), "series": series,
                  "facts": {"series": {"value": None, "source": None},
                            "channel": {"value": None, "source": None}, "time": {"value": None, "source": None},
                            "axes": {"value": None, "source": None}, "voxel_um": {"value": None, "source": None},
                            "orientation": {"value": None, "source": None}},
                  "preview": seen.get("preview") or (seen.get("outputs") or {}).get("preview"),
                  "outputs": _outputs(seen, "inspect"), "inspected": seen.get("says", "")}
        if len(series) == 1:  # one series: it is the file's; several: the user chooses, and its facts follow
            _choose_series(record, 0, {"source": "file"})
        self._write(record)
        self._journal("volume", record)
        return record

    def missing(self, record: dict, orientation: bool = True) -> list[str]:
        """What a job on this volume still needs to be told, in plain words (asked for, never guessed). `orientation`:
        a registration's; a new kind of job on volumes needs none."""
        facts, out = record["facts"], []
        if facts["series"]["value"] is None:
            shapes = "; ".join(f"{i}: {' × '.join(map(str, x.get('shape', [])))} ({x.get('axes')})"
                               for i, x in enumerate(record["series"]))
            return [f"which series to register (it holds {len(record['series'])}: {shapes})"]
        s = record["series"][facts["series"]["value"]]
        if s.get("channels", 1) > 1 and facts["channel"]["value"] is None:
            out.append(f"which channel to register (it holds {s['channels']})")
        if s.get("times", 1) > 1 and facts["time"]["value"] is None:
            out.append(f"which time point to register (it holds {s['times']})")
        if not facts["voxel_um"]["value"]:
            out.append("its voxel sizes in µm (z, y, x): the file doesn't state them")
        if orientation and not facts["orientation"]["value"]:
            out.append("its orientation, BrainGlobe's three letters for its axes (e.g. psl: towards posterior, superior "
                       "and left)")
        return out

    def describe(self, vid: str, *, voxel_um=None, orientation: str | None = None, series: int | None = None,
                 channel: int | None = None, time_point: int | None = None, session: str | None = None) -> dict:
        """What the user says of a volume, each kept as theirs with when; a file's own statement is replaced only by the
        user's word, said so."""
        record = self.get(vid, session)
        if record is None:
            raise CantDo(f"there is no volume {vid} in this session")
        facts, now = record["facts"], round(time.time(), 3)
        s = record["series"]
        if series is not None:  # first: what belonged to another series is cleared before this call's facts apply
            if not 0 <= int(series) < len(s):
                raise CantDo(f"it holds {len(s)} series: 0 to {len(s) - 1}")
            if int(series) != facts["series"]["value"]:
                _choose_series(record, int(series), {"source": "user", "at": now})
        if facts["series"]["value"] is None and (channel is not None or time_point is not None):
            raise CantDo(f"say which series first (it holds {len(s)})")
        if voxel_um is not None:
            values = [float(v) for v in voxel_um]
            if len(values) != 3 or not all(0 < v <= 1000 for v in values):
                raise CantDo("voxel sizes are three numbers in µm, z then y then x (each above 0, at most 1000)")
            facts["voxel_um"] = {"value": values, "source": "user", "at": now,
                                 **({"replaced": facts["voxel_um"]} if facts["voxel_um"].get("value") else {})}
        if orientation is not None:
            why = orientation_problem(orientation)
            if why:
                raise CantDo(why)
            facts["orientation"] = {"value": orientation.lower(), "source": "user", "at": now}
        chosen = s[facts["series"]["value"] or 0]
        for key, value, count in (("channel", channel, chosen.get("channels", 1)), ("time", time_point, chosen.get("times", 1))):
            if value is not None:
                if not 0 <= int(value) < count:
                    raise CantDo(f"it holds {count} {key}s: 0 to {count - 1}")
                facts[key] = {"value": int(value), "source": "user", "at": now}
        return self._write(record)

    # --- jobs -------------------------------------------------------------------------------------------------------
    def start(self, vid: str, *, by: str = "you", thread: str | None = None, session: str | None = None,
              again: str | None = None) -> dict:
        """Register a volume (queued at once, run in a thread; see the module's docstring). `again`: the job whose
        record this one repeats (an interrupted or failed one, run again)."""
        volume = self.get(vid, session)
        if volume is None:
            raise CantDo(f"there is no volume {vid} in this session")
        needs = self.missing(volume)
        if needs:
            raise CantDo("before it can be registered, say " + "; ".join(needs))
        m = self.entry()
        from . import env_runner

        installed = env_runner.in_use(m)
        atlas = catalog.get(ATLAS)
        atlas_dir = model_files.published(atlas) if atlas is not None else None
        if atlas_dir is None:
            raise CantDo("the Allen mouse atlas isn't downloaded yet: its Download on the Models page")
        return self._launch(volume, volume["facts"], installed, atlas_dir, atlas.version, by=by, thread=thread,
                            session=session or volume["session"], again=again, atlas_files=atlas.files)

    def _launch(self, volume: dict, facts: dict, installed, atlas_dir: Path, atlas_version: str, *, by: str,
                thread: str | None, session: str, again: str | None, atlas_files) -> dict:
        """A job of `volume` with exactly these facts, environment generation and atlas revision: queued at once, run in
        a thread."""
        from . import generation_leases

        try:
            with generation_leases.use(installed.folder):
                vid, m = volume["id"], installed.m
                receipt = json.loads((installed.folder / envs.RECEIPT).read_text())
                with self._lock:
                    jid, d = self._new_id("j", session)
                record = {"id": jid, "kind": "job", "session": session, "date": d.parent.name, "thread": thread, "by": by,
                          "at": round(time.time(), 3), "state": "queued", "entry": m.id, "version": installed.m.version,
                          "volume": {"id": vid, "name": volume["name"], "file": volume["file"], "sha256": volume["sha256"],
                                     "date": volume["date"]},
                          "facts": {k: facts[k] for k in ("series", "channel", "time", "axes", "voxel_um", "orientation")},
                          "parameters": {"atlas": "allen_mouse_25um", "backend": "niftyreg", "free_cpus": 2},
                          "environment": {"generation": installed.folder.name, "lockSha256": receipt.get("lockSha256"),
                                          "python": receipt.get("python"), "native": receipt.get("native", [])},
                          "atlas": {"id": ATLAS, "revision": atlas_dir.name, "version": atlas_version,
                                    "files": [{k: f[k] for k in ("path", "bytes", "sha256", "git_sha1") if k in f}
                                              for f in atlas_files]},
                          **({"again": again} if again else {})}
                self._write(record)
                self._journal("job", record)
                self.said(record)
                request = {"input": str(self.folder(volume) / volume["file"]),
                           "series": facts["series"]["value"] or 0, "channel": facts["channel"]["value"],
                           "time": facts["time"]["value"], "voxel_um": facts["voxel_um"]["value"],
                           "orientation": facts["orientation"]["value"], "free_cpus": 2,
                           "data": {ATLAS: str(atlas_dir)}}  # exactly this revision, the one its record names
                # Conservative preflight workspace from the actual selected series, separate from the engine baseline.
                # NiftyReg's working grids are in the 25µm atlas space. Runtime footprint/pressure guards still apply.
                import numpy as np

                selected = volume["series"][facts["series"]["value"] or 0]
                voxels = math.prod(selected["shape"])
                # A non-paged TIFF has to decode its complete series before choosing a channel/time. Reserve that larger
                # workspace conservatively, including for older inspection records without storage-layout facts.
                decoded = voxels * np.dtype(selected["dtype"]).itemsize * selected.get("channels", 1) * selected.get("times", 1)
                target_voxels = math.ceil(voxels * math.prod(facts["voxel_um"]["value"]) / 25**3)
                request["workspace_bytes"] = 2 * decoded + 48 * target_voxels
                self._start_owned_thread(record, request, installed)
                return record
        except (generation_leases.Missing, generation_leases.Busy) as exc:
            raise CantDo(str(exc)) from exc

    def start_kind(self, vid: str, key: str, *, by: str = "you", thread: str | None = None, session: str | None = None,
                   again: str | None = None, installed=None, facts: dict | None = None, workspace_origin=None) -> dict:
        """Run a new kind of job on a volume (see the module's docstring): queued at once, run in a thread. `installed`
        and `facts`: those a recorded job ran with (`again`); default the generation in use and the volume's facts now."""
        from . import env_runner

        volume = self.get(vid, session)
        if volume is None:
            raise CantDo(f"there is no volume {vid} in this session")
        m = next((e for e in env_runner.entries() if e.key == key), None)  # the user's own entries too (mine/...)
        if jobs.volume_kind(m) is None and installed is None:
            raise CantDo(f"{key} is no capability that works on volumes")
        needs = self.missing(volume, orientation=False) if facts is None else []
        if needs:
            raise CantDo(f"before {m.name} can run on it, say " + "; ".join(needs))
        installed = installed or env_runner.in_use(m)
        if installed is None:
            raise CantDo(f"{m.name} isn't installed and proven here ({env_runner.ready(m)})")
        # What it runs is the installed generation's own manifest, never today's catalog's (Codex's diagnosis of
        # 51f42854): its operation and output as that generation declares them.
        i = jobs.volume_kind(installed.m)
        if i is None:
            raise CantDo(f"{installed.m.name}'s environment {installed.folder.name} doesn't run on volumes")
        from . import generation_leases

        try:
            with generation_leases.use(installed.folder):
                facts = facts or volume["facts"]
                receipt = json.loads((installed.folder / envs.RECEIPT).read_text())
                session = session or volume["session"]
                with self._lock:
                    jid, d = self._new_id("j", session)
                record = {"id": jid, "kind": "job", "session": session, "date": d.parent.name, "thread": thread, "by": by,
                          "at": round(time.time(), 3), "state": "queued", "entry": installed.m.key, "name": installed.m.name,
                          "version": installed.m.version, "kindOf": {"operation": i.operation, "output": i.output},
                          "volume": {"id": vid, "name": volume["name"], "file": volume["file"], "sha256": volume["sha256"],
                                     "date": volume["date"]},
                          "facts": {k: facts[k] for k in ("series", "channel", "time", "axes", "voxel_um", "orientation")},
                          "parameters": {"operation": i.operation},
                          "environment": {"generation": installed.folder.name, "lockSha256": receipt.get("lockSha256"),
                                          "python": receipt.get("python"), "native": receipt.get("native", [])},
                          **({"again": again} if again else {})}
                if workspace_origin is not None:
                    from .workspace_views import checked_input
                    if (workspace_origin.get('session') != session or
                            checked_input(workspace_origin.get('input'), session) != {'volume': vid}):
                        raise CantDo('The volume changed before job admission; choose it again.')
                    record['workspaceOrigin'] = copy.deepcopy(workspace_origin)
                self._write(record)
                self._journal("job", record)
                self.said(record)
                request = {"input": str(self.folder(volume) / volume["file"]),
                           "series": facts["series"]["value"] or 0, "channel": facts["channel"]["value"],
                           "time": facts["time"]["value"], "voxel_um": facts["voxel_um"]["value"],
                           "orientation": facts["orientation"]["value"], "operation": i.operation}
                import numpy as np

                selected = volume["series"][facts["series"]["value"] or 0]
                decoded = (math.prod(selected["shape"]) * np.dtype(selected["dtype"]).itemsize * selected.get("channels", 1)
                           * selected.get("times", 1))
                request["workspace_bytes"] = 3 * decoded  # its input decoded, its own working copy and what it gives
                self._start_owned_thread(record, request, installed)
                return record
        except (generation_leases.Missing, generation_leases.Busy) as exc:
            raise CantDo(str(exc)) from exc

    def _start_owned_thread(self, record, request, installed):
        """Transfer a runtime owner before scheduling; queued work already owns its generation."""
        from . import generation_leases

        owner = generation_leases.use(installed.folder)
        owner.__enter__()
        jid = record["id"]
        key = record["session"], jid
        def run():
            try:
                self._run(record, request, installed)
            finally:
                owner.__exit__(None, None, None)
        self._stop[key] = False
        thread_ = threading.Thread(target=run, name=f"job-{jid}", daemon=True)
        self._threads[key] = thread_
        try:
            thread_.start()
        except BaseException:
            self._threads.pop(key, None)
            self._stop.pop(key, None)
            owner.__exit__(None, None, None)
            raise

    def _kind_done(self, record: dict, got: dict) -> dict:
        """What a new kind of job gave, as jobs.accept_kind checked it inside the job's bound; a volume's middle plane,
        drawn by the app there, kept as its picture (made.png, beside its outputs, its sha256 kept)."""
        d = self.folder(record)
        made = {k: got[k] for k in ("output", "file", "sha256", "bytes")}
        if got["output"] == "volume.3d":
            if got.get("picture"):
                _atomic(d / "made.png", got["picture"])
                made["picture"] = {"path": "made.png", "sha256": hashlib.sha256(got["picture"]).hexdigest()}
            made.update(shape=got["shape"], dtype=got["dtype"])
        else:
            made.update(columns=got["columns"], rows=len(got["rows"]))
        made["says"] = jobs.kind_words(got)
        return made

    def table(self, rid: str, session: str | None = None) -> dict | None:
        """A done table job's columns and rows, read back from its output by the sha256 recorded when it ended (None when
        it isn't one, or when its file is no longer what it gave)."""
        record = self.get(rid, session)
        made = (record or {}).get("made") or {}
        if record is None or record.get("state") != "done" or made.get("output") != "table.objects":
            return None
        try:
            raw = self._read_output(record, f"out/{made['file']}", expected=made["sha256"], max_bytes=jobs.MAX_TABLE_BYTES)
        except (job_outputs.Invalid, OSError):
            return None
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
        header, body = rows[0], [r for r in rows[1:] if r]
        return {"columns": [{"name": h.strip()[:80] or f"Column {n + 1}"} for n, h in enumerate(header)],
                "rows": [[jobs.table_cell(v) for v in r] for r in body], "sha256": made["sha256"],
                "producer": {"implementation": record.get("entry"), "version": record.get("version"),
                             "generation": (record.get("environment") or {}).get("generation"), "sha256": made["sha256"]},
                "title": f"{record.get('name')} on {record['volume']['name']}"}

    def _run(self, record: dict, request: dict, installed) -> None:
        from .thumbnails import background_priority

        background_priority()  # never takes the live view's time
        record.update(state="running", started=round(time.time(), 3))
        t0 = time.monotonic()
        bound = None
        try:
            d = self.files.folder(record, write=True)
            # This caller owns preparation through durable acceptance. jobs.run inherits the same original
            # deadline/cancellation; returning from the adapter does not begin a fresh publication allowance.
            with jobs.job_bound(installed.m, stop=lambda: self._stop.get((record["session"], record["id"]), False)) as bound:
                self._write(record)
                self.said(record)
                accept = ({"accept": lambda result, files: jobs.accept_kind(installed.m, result, files)}
                          if record.get("kindOf") else {})
                result = jobs.run(installed.m, request, d / "out", folder=installed.folder, entry=installed.entry,
                                  stop=lambda: self._stop.get((record["session"], record["id"]), False),
                                  started=lambda pid: (record.update(pid=pid), self._write(record)), **accept)
                outputs = _outputs(result, "out")
                if record.get("kindOf"):
                    made = self._kind_done(record, result["accepted"])
                    outcome = dict(state="done", outputs=outputs, made=made, says=made["says"])
                else:
                    outcome = dict(state="done", outputs=outputs, regions=self._regions(record, outputs.get("volumes")),
                                   says=result.get("says", ""), picture=result.get("picture"))
        except jobs.Cancelled:
            outcome = {"state": "cancelled"}
        except (jobs.Failed, job_outputs.Invalid, ProjectLocationError, InvalidProject, OSError) as exc:
            outcome = dict(state="failed", error=str(exc))
        try:
            record["resources"] = json.loads(self._read_output(record, "out/resources.json", max_bytes=1 << 20))
        except (OSError, ValueError):
            record["resources"] = {"how": "unobserved"}
        self._complete(record, outcome, bound, t0)

    def _complete(self, record, outcome, bound, started):
        """One publication owner: prepare, accept or fail, retire cancellation, then notify once.

        Readers retain running while terminal bytes are staged/replaced and checked against the original bound.
        Cancel/stop serialize with this transition; async callers perform all these waits on a worker thread.
        """
        key = record["session"], record["date"], record["id"]
        with self._lock:
            self._settling[key] = dict(record)
            if self._stop.get((record["session"], record["id"]), False) or outcome["state"] == "cancelled":
                outcome = self._cancelled()
            final = self._terminal(record, outcome, started)
            try:
                if final["state"] != "done":
                    self._failed_resources(final)
                self._write(final, bound.check if final["state"] == "done" else lambda: None)
            except (jobs.Failed, job_outputs.Invalid, ProjectLocationError, InvalidProject, OSError) as exc:
                outcome = self._cancelled() if isinstance(exc, jobs.Cancelled) else dict(state="failed", error=str(exc))
                final = self._terminal(record, outcome, started)
                self._failed_resources(final)
                try:
                    self._write(final)  # one best-effort failure record; never retry scientific publication
                except (job_outputs.Invalid, ProjectLocationError, InvalidProject, OSError) as storage:
                    final["persistence"] = {
                        "saved": False, "error": str(storage),
                        "warning": "The terminal outcome could not be saved. It is visible only in this app process; "
                                   "after restart the disk may still contain an older, unaccepted state."}
                    final["error"] += "; " + final["persistence"]["warning"]
                    final["resources"]["why"] = final["error"]
            record.clear()
            record.update(final)
            if final["state"] != "done":
                self._settling[key] = copy.deepcopy(final)
            else:
                self._settling.pop(key, None)
            self._stop.pop((record["session"], record["id"]), None)
        self.said(record)

    def _cancelled(self):
        return (dict(state="interrupted", says=INTERRUPTED) if self._stopping else
                dict(state="cancelled", says="cancelled: what it had written is kept, marked, never a result"))

    def _terminal(self, record, outcome, started):
        resources = dict(record["resources"], outcome="cancelled" if outcome["state"] == "interrupted" else outcome["state"])
        if outcome["state"] != "done":
            resources["why"] = outcome.get("error") or outcome["says"]
        final = dict(record, **outcome, resources=resources,
                     seconds=round(time.monotonic() - started, 1), ended=round(time.time(), 3))
        final.pop("pid", None)
        return final

    def _failed_resources(self, final):
        try:
            with job_outputs.Folder(self.files.folder(final, write=True) / "out") as files:
                files.write("resources.json", json.dumps(final["resources"], indent=1).encode())
        except (job_outputs.Invalid, ProjectLocationError, OSError) as exc:
            final["resources"]["persistenceError"] = str(exc)

    def _regions(self, record: dict, output: dict | None) -> list[dict]:
        """The largest regions by volume (brainreg's volumes.csv: mm³ left, right and total), largest first."""
        if output is None:
            return []
        raw = self._read_output(record, output["path"], expected=output["sha256"], max_bytes=16 << 20)
        try:
            rows = [{"name": r["structure_name"], "mm3": round(float(r["total_volume_mm3"]), 4),
                     "left": round(float(r["left_volume_mm3"]), 4), "right": round(float(r["right_volume_mm3"]), 4)}
                    for r in csv.DictReader(io.StringIO(raw.decode()))]
        except (ValueError, KeyError):
            return []
        return sorted(rows, key=lambda r: -r["mm3"])

    def cancel(self, jid: str, session: str | None = None) -> dict:
        with self._lock:
            record = self.get(jid, session)
            if record is None:
                raise CantDo(f"there is no job {jid} in this session")
            if record["state"] not in ("queued", "running"):
                raise CantDo(f"{jid} is {record['state']}, not running")
            self.files.root_for(record["session"], write=True)
            self._stop[(record["session"], jid)] = True
            return record

    def again(self, jid: str, *, by: str = "you", session: str | None = None) -> dict:
        """Run a job again as it was recorded (finding 3 of the review of 2026-10-13): the same volume file (by its
        sha256), the facts it ran with (whatever the volume says now), its environment generation (by its lockfile's
        hash) and its atlas revision. When any of them is gone or changed it is not run: what changed is said, and
        Register is the new run with what is here now."""
        record = self.get(jid, session)
        if record is None:
            raise CantDo(f"there is no job {jid} in this session")
        if record["state"] in ("queued", "running"):
            raise CantDo(f"{jid} is still {record['state']}")
        from . import env_runner

        changed = []
        volume = self.get(record["volume"]["id"], record["session"])
        if volume is None:
            changed.append(f"its volume {record['volume']['id']} is no longer in this session")
        else:
            path = self.folder(volume) / volume["file"]
            if not path.is_file() or _sha256(path) != record["volume"]["sha256"]:
                changed.append(f"{record['volume']['name']}'s file is not the one it ran on (its sha256 differs)")
        if record.get("kindOf"):
            return self._again_kind(record, changed, by=by)
        m = catalog.get(ENTRY)
        env = record.get("environment") or {}
        installed = env_runner.generation(m, env.get("generation", "")) if m is not None else None
        if installed is None:
            now = env_runner.in_use(m) if m is not None else None
            changed.append(f"brainreg's environment {env.get('generation')} is no longer here, ready and proven"
                           + (f" (the one in use now is {now.folder.name})" if now is not None else ""))
        else:
            receipt = json.loads((installed.folder / envs.RECEIPT).read_text())
            if receipt.get("lockSha256") != env.get("lockSha256"):
                changed.append(f"brainreg's environment {installed.folder.name} is not the one it ran in (its lockfile differs)")
        atlas = catalog.get(ATLAS)
        recorded = (record.get("atlas") or {}).get("revision", "")
        atlas_dir = model_files.folder(atlas) / recorded if atlas is not None and recorded else None
        files = (record.get("atlas") or {}).get("files")
        if files is None and atlas is not None and catalog.revision_of(atlas) == recorded:
            files = atlas.files  # older records are verifiable only while their exact manifest is still known
        try:
            ok = atlas_dir is not None and json.loads((atlas_dir / model_files.RECEIPT).read_text()).get("revision") == recorded
            checked = atlas_dir.parent / f".verified-{recorded}.json" if atlas_dir is not None else None
            if checked is not None and checked.exists() and json.loads(checked.read_text()).get("ok") is False:
                ok = False
            from .downloads import problem

            if ok:
                ok = bool(files) and all(
                    isinstance(f.get("path"), str) and (atlas_dir / f["path"]).resolve().is_relative_to(atlas_dir.resolve())
                    and (f.get("sha256") or f.get("git_sha1")) and not problem(atlas_dir / f["path"], f)
                    for f in files)
        except (OSError, ValueError, KeyError, TypeError):
            ok = False
        if not ok:
            changed.append(f"the atlas revision it ran with ({recorded}) is missing, changed or cannot be verified")
        if changed:
            raise CantDo(f"{jid} can't be run again as it was: " + "; ".join(changed)
                         + ". Register the volume to run it as a new job with what is here now")
        return self._launch(volume, record["facts"], installed, atlas_dir, (record.get("atlas") or {}).get("version", ""),
                            by=by, thread=record.get("thread"), session=record["session"], again=jid, atlas_files=files)

    def _again_kind(self, record: dict, changed: list[str], *, by: str) -> dict:
        """A new kind of job run again as recorded: the same volume file, facts, entry and environment generation."""
        from . import env_runner

        m = next((e for e in env_runner.entries() if e.key == record["entry"]), None)
        env = record.get("environment") or {}
        installed = env_runner.generation(m, env.get("generation", "")) if m is not None else None
        if installed is None:
            changed.append(f"{record.get('name') or record['entry']}'s environment {env.get('generation')} is no longer "
                           "here, ready and proven")
        else:
            receipt = json.loads((installed.folder / envs.RECEIPT).read_text())
            if receipt.get("lockSha256") != env.get("lockSha256"):
                changed.append(f"its environment {installed.folder.name} is not the one it ran in (its lockfile differs)")
            i = jobs.volume_kind(installed.m)  # what that generation declares, against what the record ran
            if i is None or {"operation": i.operation, "output": i.output} != record.get("kindOf"):
                changed.append(f"its environment {installed.folder.name} no longer declares what it ran "
                               f"({(record.get('kindOf') or {}).get('operation')})")
        if changed:
            raise CantDo(f"{record['id']} can't be run again as it was: " + "; ".join(changed)
                         + ". Run it on the volume again as a new job with what is here now")
        return self.start_kind(record["volume"]["id"], record["entry"], by=by, thread=record.get("thread"),
                               session=record["session"], again=record["id"], installed=installed, facts=record["facts"])

    def stop(self, wait_s: float = jobs.STOP_GRACE_S + 5) -> None:
        """The app is stopping: every job it runs is ended (its process group) and recorded as interrupted, never left
        computing on its own (its process is in a group of its own, so it would outlive the app: live 2026-10-13)."""
        with self._lock:
            self._stopping = True
            for jid in list(self._stop):
                self._stop[jid] = True
        deadline = time.monotonic() + wait_s
        for thread in list(self._threads.values()):
            thread.join(max(0.0, deadline - time.monotonic()))

    def interrupted(self) -> list[dict]:
        """At start: a job that was running when the app stopped is interrupted, said, never resumed by itself."""
        out = []
        if self.files is None:
            return out
        for record in self.files.recoverable_jobs():
            if record.get("state") in ("queued", "running"):
                self.files.root_for(record["session"], write=True)
                pid = record.pop("pid", None)
                if pid and jobs.ours(pid, self.folder(record)):
                    jobs.end(pid)
                record.update(state="interrupted", says=INTERRUPTED)
                self._write(record)
                out.append(record)
        return out

    def _read_output(self, record, name, *, expected=None, max_bytes=None):
        # Anchor at the store: date/id/out (or inspect) are walked without following directory aliases too.
        with job_outputs.Folder(self.files.root_for(record["session"])) as folder:
            return folder.read(f"{record['date']}/{record['id']}/{name}", expected=expected, max_bytes=max_bytes)

    def picture(self, rid: str, session: str | None = None, *, max_bytes: int | None = None) -> bytes | None:
        record = self.get(rid, session)
        job = rid.startswith("j")
        if record is None or (job and record.get("state") != "done"):
            return None
        key = "picture" if job else "preview"
        made = (record.get("made") or {}).get("picture") if job else None
        if made:  # a new kind's volume: its middle plane, drawn by the app, beside its outputs
            try:
                return self._read_output(record, made["path"], expected=made["sha256"], max_bytes=max_bytes)
            except job_outputs.Invalid:
                return None
        name = record.get(key)
        if not name:
            return None
        digest = ((record.get("outputs") or {}).get(key) or {}).get("sha256")
        # Older volume inspections did not retain a preview digest. Still confine their reads; new inspections bind it.
        if job and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            return None
        try:
            job_outputs.parts(name)
            return self._read_output(record, f"{'out' if job else 'inspect'}/{name}", expected=digest,
                                     max_bytes=max_bytes)
        except job_outputs.Invalid:
            return None

    def words(self, record: dict) -> str:
        """A job or a volume in plain words: what it is, where it stands, what it found."""
        if record["kind"] == "volume":
            facts = record["facts"]
            shape = record["series"][facts["series"]["value"] or 0].get("shape")
            parts = [f"{record['name']}: a volume, {' × '.join(str(n) for n in shape or [])}"]
            vox = facts["voxel_um"]
            if vox.get("value"):
                parts.append(f"voxels {' × '.join(f'{v:g}' for v in vox['value'])} µm (z y x, {vox['source']})")
            if facts["orientation"].get("value"):
                parts.append(f"orientation {facts['orientation']['value']} ({orientation_words(facts['orientation']['value'])})")
            needs = self.missing(record)
            if needs:
                parts.append("still to say: " + "; ".join(needs))
            return "; ".join(parts)
        volume = record["volume"]["name"]
        state = record["state"]
        if record.get("kindOf"):
            name = record.get("name") or record.get("entry")
            if state == "done":
                return (f"{name} on {volume} in {record.get('seconds', 0):.0f} s: it gave {record['made']['says']} "
                        "(its own output, from code nobody reviewed, read back by its sha256)")
            if state == "failed":
                return f"{name} on {volume} failed: {record.get('error')}"
            return f"{name} on {volume}: {record.get('says') or state}"
        if state == "done":
            top = ", ".join(f"{r['name']} {r['mm3']:g} mm³" for r in (record.get("regions") or [])[:REGIONS_SAID])
            return (f"{volume} registered to the Allen mouse atlas (25 µm) by brainreg in {record.get('seconds', 0):.0f} s: "
                    f"{len(record.get('regions') or [])} regions measured; the largest: {top}. Shown in brainreg's "
                    "downsampled space, as it registered it")
        if state == "failed":
            return f"Registering {volume} failed: {record.get('error')}"
        return f"Registering {volume}: {record.get('says') or state}"
