"""Trackastra in its own Python (catalog/models/trackastra: the environment the app makes, orchestrator/envs.py), spoken to
over stdin/stdout with micro-sam's wire (microsam_server.py: a JSON header and a payload, each length-prefixed).

    {"op": "load", "folder": <its catalog revision>}            -> {"ok": true, "ms": ...}
    {"op": "link", "shape": [2, H, W]} + views (float32) and labels (int32), one after the other
                                                                 -> {"ok": true, "edges": [[t, label, t, label, weight], ...]}

Its weights are the catalog's pinned revision, read with from_folder (never from_pretrained, which fetches its own). It
runs on the CPU with its torch threads capped (trackastra_link.CPU_THREADS), and reaches nothing beyond this Mac when
the app's proof asks (MICROMIND_REFUSE_NETWORK).
"""
from __future__ import annotations

import os
import sys
import time

from microsam_server import _refuse_network, read_message, write_message  # the same folder: its wire, its guard

CPU_THREADS = 2


def main() -> None:
    _refuse_network()
    out = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    stdin = sys.stdin.buffer
    model = None
    while True:
        try:
            header, payload = read_message(stdin)
        except EOFError:
            return
        try:
            op = header.get("op")
            if op == "ping":
                reply = {"ok": True}
            elif op == "load":
                t0 = time.perf_counter()
                import torch
                from pathlib import Path

                from trackastra.model import Trackastra

                torch.set_num_threads(CPU_THREADS)
                model = Trackastra.from_folder(Path(header["folder"]), device="cpu")
                reply = {"ok": True, "ms": round((time.perf_counter() - t0) * 1000, 1)}
            elif op == "link":
                import numpy as np

                if model is None:
                    raise RuntimeError("not loaded")
                shape = tuple(header["shape"])
                n = int(np.prod(shape))
                views = np.frombuffer(payload[:n * 4], dtype=np.float32).reshape(shape)
                labels = np.frombuffer(payload[n * 4:], dtype=np.int32).reshape(shape)
                graph, _ = model.track(views.copy(), labels.copy(), mode="greedy_nodiv")
                edges = []
                for n0, n1, data in graph.edges(data=True):
                    a, b = graph.nodes[n0], graph.nodes[n1]
                    w = data.get("weight")
                    edges.append([int(a.get("time")), int(a.get("label")), int(b.get("time")), int(b.get("label")),
                                  None if w is None else float(w)])
                reply = {"ok": True, "edges": edges}
            else:
                reply = {"ok": False, "error": f"unknown op {op}"}
        except Exception as exc:  # noqa: BLE001 - every failure goes back as the reply
            reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        write_message(out, reply)


if __name__ == "__main__":
    main()
