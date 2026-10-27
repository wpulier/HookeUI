"""Jobs: an entry's work that takes minutes and writes files (phase 3's E5; brainreg's registration of a brain to an
atlas), run with its own adapter in its own environment (runner python-env, an `[[implements]]` whose output is a job's:
JOB_OUTPUTS).

- **Where it runs:** `services/env_worker/job.py`, with the environment's Python, a clean environment, the network
  refused, and its native recipe's prefix as CONDA_PREFIX (envs.py), as a process group of its own: a cancel ends all
  of it, never only the parent.
- **What it reads:** its input files and the data it names (`data:<id>` in `needs_to_run`, an atlas: that entry's
  published revision, never fetched at run time), passed in its request.
- **Its proof** (Install's, and make catalog-proof's): a real run on its own fixture (the request `{"fixture": true}`),
  in a scratch folder; a new kind of job on a volume (kinds.py, C6) is given the app's own generated volume instead
  (`fixture_volume`: a 3D stack with bright spots, its facts in the request as a volume's are) and must give what it
  declared (`accept_kind`, inside the run's own deadline and cancel); outputs must be relative, owned regular files in that folder, opened without following links.
  The parent records their content hashes before returning them. Proof evidence is kept beside the
  environment as an adapter's proof is (env_runner.proof_path), so "installed and proven" means the same for both.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
from contextlib import contextmanager
import time
from pathlib import Path
from typing import Callable, Mapping

from gpu_worker import manifests as catalog
from gpu_worker import model_files

from . import envs, job_outputs, owned_process

JOB = Path(__file__).resolve().parent.parent / "env_worker" / "job.py"
JOB_OUTPUTS = {"registration.atlas"}
# What a job does, by its operation's id (an entry's `[[implements]]`): no picture operation (operations.OPERATIONS), a run
# on files that takes minutes.
OPERATIONS = {"register-atlas": "Register a whole-brain volume to an atlas"}
STOP_GRACE_S = 10.0
PROOF_LIMIT_S = 1800.0  # a proof run on its fixture, at most (brainreg's took 5-6 min on a loaded Mac): then it is ended
# Parent/protocol files in the output root are not scientific products. An adapter's nested names remain its own.
CONTROL_FILES = frozenset({"request.json", "result.json", "job.log", "resources.json"})


class Failed(RuntimeError):
    pass


class Cancelled(Failed):
    pass


class MemoryRefused(Failed):
    """Admission did not acquire memory or start the requested worker."""


class Duplicate(Failed):
    """The same job (entry, environment generation, input and settings) is already running here: this one never
    started, so nothing of it ran and nothing of the first is touched (L7b; F094: a second click was a second
    minutes-long job)."""


_RUNNING: set[str] = set()
_RUNNING_LOCK = threading.Lock()


def identity(m: catalog.Manifest, request: dict, folder: Path | None, function: str | None = None) -> str:
    """What makes two jobs the same work: its entry, the generation that runs it, what it is asked to do and on what.
    The admission workspace is how much memory to reserve, not part of the work."""
    import hashlib

    asked = {k: v for k, v in request.items() if k != "workspace_bytes"}
    text = json.dumps([m.key, str(folder or ""), function or "", asked], sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


@contextmanager
def only_once(key: str, name: str):
    """Hold `key` while its job runs; a second job with the same key is refused with Duplicate, never queued behind."""
    with _RUNNING_LOCK:
        if key in _RUNNING:
            raise Duplicate(f"{name}: the same job is already running on this input with these settings; this one was "
                            "not started (the running one carries on)")
        _RUNNING.add(key)
    try:
        yield
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(key)


def is_job(m: catalog.Manifest | None) -> bool:
    """An entry that runs as a job: registration (JOB_OUTPUTS), or a new kind of job on a volume (kinds.py)."""
    return m is not None and m.runner == "python-env" and any(
        i.output in JOB_OUTPUTS or (i.input == "volume.3d" and i.output in catalog.VOLUME_KIND_OUTPUTS)
        for i in m.implements)


def no_route(m: catalog.Manifest | None) -> str:
    """Why a contributed job (a community channel's or the user's own) can't be run here, "" when it can. A registration
    runs only with the app's own entry (registrations.ENTRY, bundled), and a contributed job runs only as a new kind of
    job on a volume (`volume_kind`): any other job installs and proves, but no control or job path would ever run it, so
    it is never said to be on (Window C, 2026-10-14: a contributed `register-atlas` giving registration.atlas read "On")."""
    if m is None or not m.namespace or not is_job(m) or volume_kind(m) is not None:
        return ""
    i = next(i for i in m.implements if i.output in JOB_OUTPUTS)  # not a volume kind: a job by its output
    why = [*([f"it takes {i.input}, not a volume"] if i.input != "volume.3d" else []),
           *([f"its output {i.output} isn't a volume or a table"] if i.output not in catalog.VOLUME_KIND_OUTPUTS else [])]
    return f"Can't be turned on here: no way to run a contributed {i.operation!r} job yet ({'; '.join(why)})"


# --- a new kind of job on a volume (kinds.py; C6): its input is one of the user's volumes, its output a file the app checks
MAX_VOLUME_BYTES = 1 << 30  # a volume it gives, read back at most (1 GiB)
MAX_VOLUME_SIDE = 4096
MAX_TABLE_BYTES = 16 << 20
FIXTURE_VOXEL_UM = [2.0, 0.5, 0.5]  # z, y, x: the generated volume's own, stated in its request
FIXTURE_SPOTS = ((6, 20, 24), (10, 64, 40), (14, 30, 80), (18, 76, 76), (12, 48, 60))


def volume_kind(m: catalog.Manifest | None):
    """The `[[implements]]` of an entry that runs on a volume (input volume.3d), or None."""
    if m is None or m.runner != "python-env":
        return None
    return next((i for i in m.implements if i.input == "volume.3d" and i.output in catalog.VOLUME_KIND_OUTPUTS), None)


def fixture_volume(path: Path) -> dict:
    """The app's own example volume (z 24, y 96, x 96, uint16): a dim noisy background with five bright spots of known
    places, written as one plane a page. Its facts, as a volume's are given to a job."""
    import numpy as np
    import tifffile

    rng = np.random.default_rng(7)
    z, y, x = np.mgrid[0:24, 0:96, 0:96]
    volume = rng.normal(400, 40, (24, 96, 96))
    for cz, cy, cx in FIXTURE_SPOTS:
        volume += 3000 * np.exp(-(((z - cz) * 2.0) ** 2 + ((y - cy) * .5) ** 2 + ((x - cx) * .5) ** 2) / (2 * 2.5 ** 2))
    tifffile.imwrite(path, np.clip(volume, 0, 65535).astype(np.uint16))
    return {"input": str(path), "series": 0, "channel": None, "time": None, "voxel_um": list(FIXTURE_VOXEL_UM),
            "orientation": None, "spots": [list(c) for c in FIXTURE_SPOTS]}


PLANE_MAX_BYTES = 128 << 20  # one plane of a volume it gives, decoded, at most (4096 x 4096 float64)
INTEGER = re.compile(r"-?(0|[1-9][0-9]*)")
EXACT_INTEGER = 2 ** 53  # past this a number is not exact in the studio's JavaScript: kept as its text


def table_cell(v: str):
    """A CSV cell as a number only when the number reads back exactly as written (an integer within EXACT_INTEGER, or a
    decimal whose shortest form is the text itself); otherwise its text, exactly as written (Codex's diagnosis of
    51f42854: 9007199254740993 or an id with a leading zero is never changed into another value)."""
    text = v.strip()
    if INTEGER.fullmatch(text):
        n = int(text)
        return n if abs(n) <= EXACT_INTEGER else v
    try:
        f = float(text)
    except ValueError:
        return v
    return f if f == f and abs(f) != float("inf") and repr(f) == text else v


def accept_kind(m: catalog.Manifest, result: dict, folder) -> dict:
    """What a volume kind gave, checked against what it declared, inside its job's own bound (jobs.run's `accept`: the
    same deadline and cancel as its run, every read through the job's output folder, which checks them), read by the
    sha256 recorded when its process ended (Codex's diagnosis of 51f42854). A volume (`outputs.volume`: a TIFF of three
    spatial axes stored a plane a page) is admitted from its headers before anything is decoded (its encoded bytes and
    the decoded dtype and preview workspace, JobMemory) and checked one plane at a time; its middle plane drawn by the app as a PNG. A table
    (`outputs.table`, a CSV with a header) is read whole within its limits, its cells exact (table_cell). Returns
    {"output", "file", "sha256", "bytes", ...}; Failed (or Cancelled) otherwise."""
    import csv
    import io

    from . import kinds

    i = volume_kind(m)
    if i is None:
        raise Failed(f"{m.name} doesn't run on a volume")
    key = "volume" if i.output == "volume.3d" else "table"
    name = (result.get("outputs") or {}).get(key)
    identity = (result.get("outputFiles") or {}).get(name) if isinstance(name, str) else None
    if identity is None:
        raise Failed(f"{m.name} declared {i.output} but named no `{key}` output")
    said = {"output": i.output, "file": name, "sha256": identity["sha256"], "bytes": identity["bytes"]}
    if key == "table":
        if identity["bytes"] > MAX_TABLE_BYTES:
            raise Failed(f"{m.name}: its table is larger than {MAX_TABLE_BYTES >> 20} MB")
        data = _read(m, folder, name, identity["sha256"], MAX_TABLE_BYTES)
        try:
            rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        except (UnicodeDecodeError, csv.Error) as exc:
            raise Failed(f"{m.name}: its table {name} isn't a CSV of text ({type(exc).__name__})") from exc
        if not rows or not rows[0]:
            raise Failed(f"{m.name}: its table {name} has no header")
        header, body = rows[0], [r for r in rows[1:] if r]
        if len(header) > kinds.MAX_COLUMNS or len(body) > kinds.MAX_ROWS:
            raise Failed(f"{m.name}: its table has {len(body)} rows of {len(header)} columns (at most {kinds.MAX_ROWS} "
                         f"of {kinds.MAX_COLUMNS})")
        if any(len(r) != len(header) for r in body):
            raise Failed(f"{m.name}: its table's rows don't all have its header's {len(header)} columns")
        return {**said, "columns": [{"name": h.strip()[:80] or f"Column {n + 1}"} for n, h in enumerate(header)],
                "rows": [[table_cell(v) for v in r] for r in body]}
    if identity["bytes"] > MAX_VOLUME_BYTES:
        raise Failed(f"{m.name}: its volume is larger than {MAX_VOLUME_BYTES >> 20} MB")
    # Folder.read briefly holds its chunks and joined bytes together; BytesIO may also copy the returned bytes.
    # Reserve both encoded copies before the first read, separately from the dtype-dependent decode workspace.
    with MEMORY.reserve(2 * identity["bytes"] / 2 ** 20 + 16):
        data = _read(m, folder, name, identity["sha256"], MAX_VOLUME_BYTES)
        import numpy as np
        import tifffile

        try:
            with tifffile.TiffFile(io.BytesIO(data)) as tf:
                series = tf.series[0]
                spatial = [n for a, n in zip(series.axes, series.shape) if a not in "CTS"]
                if len(spatial) != 3 or len(series.shape) != 3 or any(not 0 < n <= MAX_VOLUME_SIDE for n in spatial):
                    raise Failed(f"{m.name} gave {series.axes} {tuple(series.shape)}, not one volume of three spatial "
                                 f"axes (each at most {MAX_VOLUME_SIDE})")
                dtype = np.dtype(series.dtype)
                if dtype.kind not in "uif":
                    raise Failed(f"{m.name} gave a volume of {dtype}, not numbers")
                depth, plane_shape = int(series.shape[0]), tuple(int(n) for n in series.shape[1:])
                plane_bytes = plane_shape[0] * plane_shape[1] * dtype.itemsize
                pages = series.pages
                if len(pages) != depth or tuple(series.keyframe.shape) != plane_shape:
                    raise Failed(f"{m.name}'s volume isn't stored a plane a page, so it can't be checked a plane at a "
                                 "time")
                if plane_bytes > PLANE_MAX_BYTES:
                    raise Failed(f"{m.name}'s volume has planes of {plane_bytes >> 20} MB (at most "
                                 f"{PLANE_MAX_BYTES >> 20} MB)")
                pixels = plane_shape[0] * plane_shape[1]
                # Source plus decoder scratch, three float32 planes (retained middle, percentile copy and
                # numerical scratch), and four byte planes (finite/grey/encoded buffers), with codec overhead.
                # The source dtype alone is not the workspace: uint8 expands fourfold for the preview.
                workspace = 2 * plane_bytes + 16 * pixels + (16 << 20)
                with MEMORY.reserve(workspace / 2 ** 20):
                    middle = None
                    for z in range(depth):
                        folder.check()  # the job's deadline and cancel, between planes
                        plane = pages[z].asarray()
                        if plane.shape != plane_shape:
                            raise Failed(f"{m.name}'s volume has a plane of another size")
                        if dtype.kind == "f" and not np.isfinite(plane).all():
                            raise Failed(f"{m.name} gave a volume with values that aren't numbers")
                        if z == depth // 2:
                            middle = plane.astype(np.float32)
                        del plane
                    lo, hi = np.percentile(middle, (1, 99.5)) if middle.size else (0, 1)
                    middle -= lo
                    middle *= 255 / max(float(hi - lo), 1e-9)
                    np.clip(middle, 0, 255, out=middle)
                    grey = middle.astype(np.uint8)
                    del middle
                    import cv2

                    ok, png = cv2.imencode(".png", grey)
                    picture = png.tobytes() if ok else None  # the encoded copy is still inside its reservation
        except (Failed, owned_process.Stopped):
            raise
        except Exception as exc:  # noqa: BLE001 - any unreadable TIFF, in the app's words
            raise Failed(f"{m.name}: its volume {name} isn't a readable TIFF ({type(exc).__name__})") from exc
    folder.check()
    return {**said, "shape": [depth, *plane_shape], "dtype": str(dtype), "picture": picture}


def _read(m, folder, name: str, sha256: str, most: int) -> bytes:
    try:
        return folder.read(name, expected=sha256, max_bytes=most)
    except job_outputs.Invalid as exc:
        raise Failed(f"{m.name}: its output {name}: {exc}") from exc


def kind_words(got: dict) -> str:
    if got["output"] == "volume.3d":
        return f"a volume {' × '.join(map(str, got['shape']))} ({got['dtype']})"
    n = len(got["rows"])
    return f"a table of {n} row{'s' if n != 1 else ''} ({', '.join(c['name'] for c in got['columns'][:6])})"


def data_dirs(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict[str, str]:
    """{data id: its published folder} for every `data:<id>` it needs; Failed with what to download otherwise."""
    out = {}
    for need in m.needs_to_run:
        kind, _, what = need.partition(":")
        if kind != "data":
            continue
        d = catalog.get(what)
        found = model_files.published(d, environ) if d is not None else None
        if found is None:
            name = d.name if d is not None else what
            raise Failed(f"{name} isn't downloaded (its Download on the Models page, or make catalog-download ID={what})")
        out[what] = str(found)
    return out


def env_for(m: catalog.Manifest, folder: Path, environ: Mapping[str, str] = os.environ) -> dict:
    env = envs.clean_env(m, folder, environ)
    env["MICROMIND_REFUSE_NETWORK"] = "1"  # its code never reaches the network: what it needs is pinned
    return env


class JobMemory:
    """Reserve external job peaks against the same system free-memory source/margin as model loads.

    Separate from Capture's retained-image allowance. Reservations cover concurrent jobs in this process; observed
    pressure and footprint remain runtime guards, not a claim that an input-size estimate proves an arbitrary adapter.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self.reserved_mb = 0.0

    @contextmanager
    def reserve(self, mb):
        from . import machine
        from .loads import MARGIN_MB

        with self._lock:
            free = machine.free_mb()
            if free is None or free < self.reserved_mb + mb + MARGIN_MB:
                raise MemoryRefused(f"not enough free memory for this job: needs {mb:.0f} MB plus the live-view margin; "
                             + ("free memory is unknown" if free is None else f"{free:.0f} MB is free")
                             + "; wait for other work to finish")
            self.reserved_mb += mb
        try:
            yield
        finally:
            with self._lock:
                self.reserved_mb = max(0.0, self.reserved_mb - mb)


MEMORY = JobMemory()


def process_memory_mb(pid: int) -> tuple[float | None, str]:
    """Sample only the owned group, native children included. Physical footprint on macOS, else resident size."""
    from gpu_worker.footprint import footprint_mb

    try:
        lines = subprocess.run(["/bin/ps", "-axo", "pid=,pgid=,rss="], capture_output=True, text=True,
                               timeout=2).stdout.splitlines()
        group = [(int(p), float(rss) / 1024) for line in lines if len(parts := line.split()) == 3
                 for p, pgid, rss in [parts] if int(pgid) == pid]
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, "unavailable"
    if not group:
        return None, "unavailable"
    footprints = [footprint_mb(p) for p, _ in group]
    if all(value is not None for value in footprints):
        return sum(footprints), "process-group physical footprint"
    return sum(rss for _, rss in group), "process-group resident size"


def run(m: catalog.Manifest, request: dict, out_dir: Path, **kwargs) -> dict:
    """Admit an owned job before starting it and keep its sampled resource evidence, including failures."""
    from . import env_runner, generation_leases, licences

    environ = kwargs.get("environ", os.environ)
    if kwargs.get("folder") is None or kwargs.get("entry") is None:
        installed = env_runner.in_use(m, environ)
        if installed is None:
            raise Failed(f"{m.name} isn't installed and proven here ({env_runner.ready(m, environ)})")
        m = installed.m
        kwargs.update(folder=installed.folder, entry=installed.entry)
    refused = licences.why_not(licences.model_uses(m), environ=environ)
    if refused:
        raise Failed(refused)  # a saved/replay data path never bypasses its component's current edition restriction
    # The manifest's runtime baseline plus the selected volume's decode/resampling workspace. The latter is prepared
    # from inspected dimensions by Registrations, and bounded independently in the adapter before pixel allocation.
    mb = max(256, m.peak_mb, m.documented_mb) + max(0, int(request.get("workspace_bytes", 0))) / 2**20
    resources = {"reservedMb": round(mb, 3), "peakMb": None, "how": "unobserved", "samples": 0, "outcome": "failed"}
    # A proof runs on its own scratch fixture; every other job is one at a time per identity (only_once).
    once = contextlib.nullcontext() if request.get("fixture") else only_once(
        identity(m, request, kwargs["folder"], kwargs.get("function")), m.name)
    with once:
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            with generation_leases.use(kwargs["folder"]), job_outputs.Folder(out_dir) as output_files:
                return _admitted(m, request, out_dir, resources, mb, output_files, kwargs)
        except (job_outputs.Invalid, generation_leases.Missing, generation_leases.Busy) as exc:
            raise Failed(f"{m.name}: {exc}") from exc


def _admitted(m, request, out_dir, resources, mb, output_files, kwargs):
    try:
        with job_bound(m, kwargs.get("limit_s"), kwargs.get("stop", lambda: False)) as bound:
            output_files.check = bound.check
            accept = kwargs.pop("accept", None)
            with MEMORY.reserve(mb):
                result = _run(m, request, out_dir, resources=resources, output_files=output_files, **kwargs)
            bound.check()
            if accept is not None:  # what it gave, checked inside the same deadline and cancel (Codex, 51f42854)
                result = {**result, "accepted": accept(result, output_files)}
                bound.check()
            resources["outcome"] = "done"
            output_files.write("resources.json", json.dumps(resources, indent=1).encode())
            bound.check()
            return {**result, "resources": resources}
    except BaseException as exc:
        resources["outcome"] = "cancelled" if isinstance(exc, Cancelled) else "failed"
        resources["why"] = str(exc)
        if isinstance(exc, Failed):
            exc.resources = dict(resources)
        output_files.write("resources.json", json.dumps(resources, indent=1).encode())
        raise


def _stop_error(m, limit, exc):
    if "cancelled" in str(exc):
        return Cancelled("cancelled")
    return Failed(f"{m.name} ran past its limit of {round(limit)} s and was ended")


class JobBound:
    """A retained job deadline whose checks always use the job's public failure contract."""
    def __init__(self, bound, m, limit):
        self._bound, self._model, self._limit = bound, m, limit

    def check(self):
        try:
            self._bound.check()
        except owned_process.Stopped as exc:
            raise _stop_error(self._model, self._limit, exc) from exc


@contextmanager
def job_bound(m, limit_s=None, stop=lambda: False):
    """One deadline/cancellation from admission through output and durable result acceptance."""
    limit = limit_s if limit_s is not None else PROOF_LIMIT_S
    try:
        with owned_process.scope(seconds=limit, stop=stop) as bound:
            yield JobBound(bound, m, limit)
    except owned_process.Stopped as exc:
        raise _stop_error(m, limit, exc) from exc


def _run(m: catalog.Manifest, request: dict, out_dir: Path, *, resources: dict, output_files: job_outputs.Folder,
         folder: Path | None = None, entry: Path | None = None,
        environ: Mapping[str, str] = os.environ, stop: Callable[[], bool] = lambda: False,
        started: Callable[[int], None] = lambda pid: None, function: str | None = None,
        limit_s: float | None = None) -> dict:
    """Run the entry's adapter once on `request` into `out_dir` (see the module's docstring). `folder` and `entry` are
    the environment generation and the copy of the entry to run (default: the one in use); `function` another function
    of its adapter's module (brainreg's `inspect`, which reads what a volume's file states). Returns its result; Failed
    with its words, Cancelled when `stop` said so. `limit_s`: past it the whole process group is ended and it fails,
    said."""
    from . import env_runner

    if folder is None or entry is None:
        installed = env_runner.in_use(m, environ)
        if installed is None:
            raise Failed(f"{m.name} isn't installed and proven here ({env_runner.ready(m, environ)})")
        folder, entry, m = installed.folder, installed.entry, installed.m
    out_dir.mkdir(parents=True, exist_ok=True)
    files = model_files.published(m, environ) if m.files else None
    if m.files and files is None:
        raise Failed(f"{m.name}'s files aren't downloaded")
    # The data it reads: the request's own (a job run again as it was names the revisions it ran with), else the ones in use.
    data = request.get("data") or data_dirs(m, environ)
    full = {**request, "output": str(out_dir), "data": data, "files": str(files) if files else ""}
    request_path = out_dir / "request.json"
    output_files.write("request.json", json.dumps(full, indent=1).encode())
    (out_dir / "result.json").unlink(missing_ok=True)
    with (out_dir / "job.log").open("ab") as log:
        target = m.adapter if function is None else f"{m.adapter.partition(':')[0]}:{function}"
        cmd = [str(folder / "bin" / "python"), "-I", str(JOB), str(entry), target, str(request_path)]
        env = env_for(m, folder, environ)
        from . import sandbox

        if sandbox.required(m):  # code nobody reviewed: inside macOS's sandbox, or not at all (C7)
            if not sandbox.available():
                raise Failed(sandbox.refused(m))
            scratch = out_dir / ".scratch"  # its home, temporary folder and caches: apart from what it outputs
            scratch.mkdir(exist_ok=True)
            env = sandbox.confine(env, scratch)
            cmd = sandbox.wrap(cmd, read=[folder, sandbox.python_home(folder / "bin" / "python"), entry, *sandbox.bootstrap(),
                                          *(str(v) for v in data.values()), str(files) if files else "",
                                          *(str(p) for p in ([request["input"]] if request.get("input") else []))],
                               write=[out_dir])
        process = subprocess.Popen(cmd, cwd=str(out_dir), env=env,
                                   stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            started(process.pid)
            with owned_process.scope(stop=stop), owned_process.watching(
                    process, limit_s if limit_s is not None else PROOF_LIMIT_S, m.name):
                from . import machine
                from .loads import MARGIN_MB

                while process.poll() is None:
                    used, how = process_memory_mb(process.pid)
                    if used is not None:
                        resources.update(peakMb=max(resources["peakMb"] or 0, used), how=how,
                                         samples=resources["samples"] + 1)
                        if used > resources["reservedMb"]:
                            raise Failed(f"{m.name} exceeded its reserved memory ({used:.0f} MB observed, "
                                         f"{resources['reservedMb']:.0f} MB reserved); job ended, partial outputs kept")
                    free = machine.free_mb()
                    if free is not None and free < MARGIN_MB:
                        raise Failed(f"free memory fell below the live-view margin ({free:.0f} MB); job ended")
                    try:
                        process.wait(timeout=.5)
                    except subprocess.TimeoutExpired:
                        pass
        except owned_process.Stopped as exc:
            if "cancelled" in str(exc):
                raise Cancelled("cancelled") from exc
            raise Failed(f"{m.name} ran past its limit of {round(limit_s or PROOF_LIMIT_S)} s and was ended") from exc
        finally:
            owned_process.terminate(process)
            process.stdin.close()
    try:
        result = json.loads(output_files.read("result.json", max_bytes=8 << 20))
    except (OSError, ValueError):
        words = last_words(out_dir, output_files=output_files)
        raise Failed(f"{m.name} stopped without a result (exit {process.returncode}): {words}") from None
    if not isinstance(result, dict):
        raise Failed(f"{m.name}: invalid output result")
    if not result.get("ok"):
        raise Failed(f"{m.name}: {result.get('error') or last_words(out_dir, output_files=output_files)}")
    outputs = result.get("outputs") or {}
    if not isinstance(outputs, dict):
        raise Failed(f"{m.name}: outputs must name regular files")
    names = list(outputs.values()) + [result[k] for k in ("picture", "preview") if result.get(k) is not None]
    identities = {}
    for name in names:
        try:
            if isinstance(name, str) and name.casefold() in CONTROL_FILES:
                raise job_outputs.Invalid(f"output {name!r} is reserved for a job control file")
            identities[name] = output_files.identity(name)
        except (job_outputs.Invalid, TypeError) as exc:
            detail = (f"said it wrote {name}, which isn't there" if isinstance(exc.__cause__, FileNotFoundError)
                      else str(exc))
            raise Failed(f"{m.name}: invalid output: {detail}") from exc
    # The parent computes these after the whole owned process group has ended; a worker cannot supply receipts.
    return {**result, "outputFiles": identities}


def ours(pid: int, folder: Path) -> bool:
    """Whether a process still alive is this app's job runner working in `folder` (a pid kept in a record may since be
    another program's: only ours is ever ended)."""
    try:
        done = subprocess.run(["/bin/ps", "-o", "command=", "-p", str(int(pid))], capture_output=True, text=True,
                              timeout=5)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return str(JOB) in done.stdout and str(folder) in done.stdout


def end(pid: int) -> None:
    """The job's whole process group: asked to stop, then made to."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + STOP_GRACE_S
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.2)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def last_words(out_dir: Path, n: int = 400, *, output_files=None) -> str:
    try:
        if output_files is None:
            with job_outputs.Folder(out_dir) as opened:
                return last_words(out_dir, n, output_files=opened)
        text = output_files.read("job.log", max_bytes=8 << 20).decode(errors="replace")
    except job_outputs.Invalid:
        return "no readable log"
    return (envs.last_reason(text) or "no words")[-n:]  # a library macOS wouldn't load: which, and why


def prove(m: catalog.Manifest, environ: Mapping[str, str] = os.environ,
          say: Callable[[str], None] = lambda s: None, stop: Callable[[], bool] = lambda: False,
          limit_s: float = PROOF_LIMIT_S) -> dict:
    """A real run on its own fixture with the environment just made (envs.folder), checked and kept beside it: ended
    with the Install that asked for it (`stop`), and past `limit_s`."""
    from . import env_runner

    t0 = time.monotonic()
    folder = envs.folder(m, environ)
    entry = folder / envs.SNAPSHOT / m.id
    try:
        if not entry.is_dir():
            raise Failed("its environment keeps no copy of the entry: install it again")
        with tempfile.TemporaryDirectory(prefix=f"proof-{m.id}-") as tmp:
            i = volume_kind(m)
            request = {"fixture": True}
            if i is not None:  # a new kind on a volume: the app's own example volume, as a user's volume is given
                say("proving it on the app's example volume")
                request.update(fixture_volume(Path(tmp) / "example-volume.tif"), operation=i.operation)
            else:
                say("proving it on its own example (a real run: minutes)")
            r = run(m, request, Path(tmp) / "out", folder=folder, entry=entry, environ=environ, stop=stop,
                    limit_s=limit_s, **({"accept": lambda result, files: accept_kind(m, result, files)} if i else {}))
            made = ""
            if i is not None:
                got = r["accepted"]
                made = f"made {kind_words(got)} from the app's example volume (24 × 96 × 96), sha256 {got['sha256'][:12]}"
            result = {"ok": True, "says": made or r.get("says") or f"ran on its example in {round(time.monotonic() - t0)} s",
                      "outputs": sorted(r.get("outputs", {})), "resources": r.get("resources", {}), "seconds": round(time.monotonic() - t0, 1),
                      **({"input": "volume.3d (the app's example, 24 × 96 × 96 uint16)", "output": i.output} if i else {})}
    except Cancelled:
        raise  # the Install was cancelled: no proof is kept
    except Failed as exc:
        result = {"ok": False, "why": str(exc)}
        if getattr(exc, "resources", None):
            result["resources"] = exc.resources
    result["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    path = env_runner.proof_path(m, environ)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(result))
    tmp_path.replace(path)
    return result
