"""How a GPU worker process starts: its CPU threads capped and its priority set by its role, before
anything heavy is loaded.

Live 2026-10-11 02:36-02:39: no worker capped its threads or lowered its priority. Every library in every
worker (torch, OpenMP, Accelerate, OpenCV) started one thread per core, micro-sam's own Python did the
same, and while it loaded beside the machine's other work the load reached 142 on 14 cores: frame
analysis went from 14 ms to 2.2 s and the follow worker answered nothing for 42 s.

- **Thread caps** (THREADS, by role): OMP/MKL/VECLIB/OPENBLAS/NUMEXPR_NUM_THREADS, read by the libraries
  when they load, set by the parent around the spawn (`spawn_env`: a spawned child imports the parent's
  main module, numpy with it, before its target runs) and again here; cv2.setNumThreads here;
  torch.set_num_threads right after torch's import (worker.Models). A process the worker starts
  (micro-sam's Python) inherits them. The live roles (clicks, following) get more than the heavy ones.
- **Priority** (PRIORITY, by role): "nice" is os.nice(NICE); "background" is macOS's background policy for
  the process (PRIO_DARWIN_BG, as `taskpolicy -b`: efficiency cores and throttled disk). Chosen by what each
  costs our own requests (bench/priority_bench.py, docs/bench/priority.md): the workers keep normal priority
  (their caps hold the live view), and micro-sam's own Python is niced (microsam_cells.py).

MICROMIND_<ROLE>_THREADS and MICROMIND_<ROLE>_PRIORITY override one role (the bench measures with them).
A client with no role (a bench's own worker) starts as before: no caps, no priority.
"""
from __future__ import annotations

import contextlib
import os
import sys
import threading

ROLES = ("main", "follow", "heavy", "molmo")
THREADS = {"main": 4, "follow": 4, "heavy": 2, "molmo": 2}
# Measured (docs/bench/priority.md): background policy starved our own work (loads 2.5-3x slower on a quiet Mac,
# no progress in 90 s under load); nice on the whole analysis worker slowed its CPU-bound requests under an
# outside load past the 20% rule (automatic masks +40%, embeddings +49%). So the workers run capped at normal
# priority, and only micro-sam's own Python, where a cold load is the heaviest CPU work, is niced
# (noncommercial/microsam_cells.py: +11% under load, faster when quiet).
PRIORITY = {"main": "", "follow": "", "heavy": "", "molmo": ""}
NICE = 10
ENV_KEYS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS",
            "NUMEXPR_NUM_THREADS", "MICROMIND_GPU_THREADS")
PRIO_DARWIN_PROCESS, PRIO_DARWIN_BG = 4, 0x1000  # <sys/resource.h>
_env_lock = threading.Lock()


def threads(role: str | None, environ=os.environ) -> int | None:
    """The CPU threads a role may use (None: no cap)."""
    if role not in ROLES:
        return None
    value = environ.get(f"MICROMIND_{role.upper()}_THREADS")
    try:
        n = int(value) if value else THREADS[role]
    except ValueError:
        n = THREADS[role]
    return n if n > 0 else None


def priority(role: str | None, environ=os.environ) -> str:
    """How a role's process lowers its priority: "nice", "background" or "" (not at all)."""
    if role not in ROLES:
        return ""
    value = environ.get(f"MICROMIND_{role.upper()}_PRIORITY")
    value = PRIORITY[role] if value is None else value.strip().lower()
    return value if value in ("nice", "background") else ""


def caps(role: str | None, environ=os.environ) -> dict[str, str]:
    n = threads(role, environ)
    return {key: str(n) for key in ENV_KEYS} if n else {}


@contextlib.contextmanager
def spawn_env(role: str | None):
    """The role's caps in this process's environment while a worker is spawned (it starts with them), put
    back after. Workers are spawned from the event loop one at a time; the lock keeps two apart anyway."""
    values = caps(role)
    with _env_lock:
        saved = {key: os.environ.get(key) for key in values}
        os.environ.update(values)
        try:
            yield
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def lower_priority(how: str) -> str:
    """Lower this process's CPU priority; what was done ("" when nothing was)."""
    try:
        if how == "background" and sys.platform == "darwin":
            os.setpriority(PRIO_DARWIN_PROCESS, 0, PRIO_DARWIN_BG)
            return how
        if how in ("nice", "background"):
            os.nice(NICE)
            return "nice"
    except OSError:
        pass
    return ""


def serve(conn, role: str | None = None) -> None:
    """The spawn target: caps and priority first, then the worker (gpu_worker/worker.py). A worker never fetches a
    model: its pinned files are in the models folder or an old cache (model_files.py), and a library that reaches for
    the Hub (a backbone's config) reads its cache, offline."""
    os.environ.update(caps(role))
    os.environ["HF_HUB_OFFLINE"] = "1"
    from .offline import refuse

    refuse()  # make catalog-proof: nothing here may reach beyond this Mac (offline.py)
    n = threads(role)
    if n:
        import cv2

        cv2.setNumThreads(n)
    lowered = lower_priority(priority(role))
    if role:
        print(f"[gpu-worker] {role}: {n or 'all'} CPU threads, priority {lowered or 'normal'}", file=sys.stderr, flush=True)
    from gpu_worker.worker import serve as run

    run(conn, role)
