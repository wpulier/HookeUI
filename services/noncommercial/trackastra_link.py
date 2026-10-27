"""Trackastra (weigertlab/trackastra; code BSD-3-Clause): links cells between two views with its pretrained
general_2d model (operation "link", operations.LINK).

Kept with the non-commercial components (ADR 0005) until two things are known: the terms of the data its
general_2d weights were trained on (upstream states none, and Cellpose's weights were trained on data licensed
for non-commercial use only), and Gurobi, the proprietary solver its install brings through motile[gurobi],
which is never called here (linking is greedy). Reached only through licences.adapter("trackastra").
"""
from __future__ import annotations

import threading

TRACKASTRA_MODEL = "general_2d"  # its pretrained weights, by Trackastra's own name
VERSION = "0.5.6"  # what `make trackastra-setup` installs: its track() returns (graph, tracked masks)


def catalog_env():
    """The Python the app made for Trackastra from its catalog lockfile (orchestrator/envs.py), when it is ready."""
    from gpu_worker import manifests as catalog
    from orchestrator import envs

    m = catalog.get("trackastra")
    return envs.python(m) if m is not None else None


def installed_version() -> str | None:
    """The Trackastra actually installed, for the receipt (never the pinned number said as if it were): its environment's
    lockfile pins it exactly, else the one in the app's own Python."""
    if catalog_env() is not None:
        from gpu_worker import manifests as catalog
        from orchestrator import envs

        for line in envs.lock_file(catalog.get("trackastra")).read_text().splitlines():
            if line.startswith("trackastra=="):
                return line.split("==", 1)[1].split()[0]
    try:
        from importlib.metadata import version

        return version("trackastra")
    except Exception:
        return None

_model = None
_lock = threading.Lock()
# It runs on the CPU in the app's own process (a thread beside the live view's frame analysis), so its torch work is
# capped like the heavy GPU worker's (gpu_worker/boot.py): left to itself torch takes one thread per core (live
# 2026-10-11: uncapped work beside the live view took frame analysis from 14 ms to 2.2 s).
CPU_THREADS = 2


def model():
    global _model
    with _lock:
        if _model is None:
            import torch
            from trackastra.model import Trackastra

            from gpu_worker.model_files import model_dir

            # Its weights are the catalog's pinned revision (catalog/models/trackastra), read from that folder alone:
            # from_pretrained would fetch them itself, unpinned, into its own cache (the catalog audit, 2026-10-12).
            folder = model_dir("trackastra")
            torch.set_num_threads(CPU_THREADS)
            _model = Trackastra.from_folder(folder, device="cpu")
        return _model


class _OwnPython:
    """Trackastra in the Python the app made for it (trackastra_server.py), started on first use and kept: the app's own
    Python never needs its packages (Gurobi among them)."""

    def __init__(self) -> None:
        self._process = None
        self._lock = threading.Lock()

    def _start(self, exe):
        import os
        import subprocess
        from pathlib import Path

        from gpu_worker.model_files import model_dir
        from orchestrator import envs
        from noncommercial.microsam_server import read_message, write_message

        if self._process is not None and self._process.poll() is None:
            return self._process
        server = Path(__file__).with_name("trackastra_server.py")
        env = {**envs.clean_env(d=exe.parent.parent), **{k: v for k, v in os.environ.items() if k.endswith("_NUM_THREADS")}}
        self._process = subprocess.Popen([str(exe), str(server)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         cwd=str(server.parent), env=env)
        write_message(self._process.stdin, {"op": "load", "folder": str(model_dir("trackastra"))})
        reply, _ = read_message(self._process.stdout)
        if not reply.get("ok"):
            self._process.kill()
            self._process = None
            raise RuntimeError(f"Trackastra didn't start in its own Python: {reply.get('error')}")
        return self._process

    def link(self, exe, views, labels) -> list[tuple]:
        import numpy as np

        from noncommercial.microsam_server import read_message, write_message

        with self._lock:
            process = self._start(exe)
            try:
                payload = np.ascontiguousarray(views, np.float32).tobytes() + np.ascontiguousarray(labels, np.int32).tobytes()
                write_message(process.stdin, {"op": "link", "shape": list(views.shape)}, payload)
                reply, _ = read_message(process.stdout)
            except (EOFError, BrokenPipeError, OSError) as exc:
                self._process = None
                raise RuntimeError(f"Trackastra's own Python stopped ({type(exc).__name__})") from exc
        if not reply.get("ok"):
            raise RuntimeError(f"Trackastra: {reply.get('error')}")
        return [tuple(e) for e in reply["edges"]]


_own = _OwnPython()


def link(views, labels) -> list[tuple]:
    """Every edge of Trackastra's greedy graph on two frames, as (time, label, time, label, weight or None).
    `views`: (2, H, W) float32; `labels`: (2, H, W) int32, each frame's exclusive label map. In the Python the app made
    for it when there is one (its catalog environment), else in the app's own (`make trackastra-setup`, before)."""
    exe = catalog_env()
    if exe is not None:
        return _own.link(exe, views, labels)
    graph, _ = model().track(views, labels, mode="greedy_nodiv")  # same object means no divisions (M3.5's re-check)
    out = []
    for n0, n1, data in graph.edges(data=True):
        a, b = graph.nodes[n0], graph.nodes[n1]
        weight = data.get("weight")
        out.append((a.get("time"), a.get("label"), b.get("time"), b.get("label"), None if weight is None else float(weight)))
    return out
