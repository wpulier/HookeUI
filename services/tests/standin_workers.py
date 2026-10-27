"""Stand-in GPU workers that answer at once, as the real worker answers each op (gpu_worker/worker.py), and keep what
they were asked: which op, which model, which priority. For checks of the wiring (tests/test_capability_matrix.py,
tests/test_model_checks.py): the request reached the implementation it named, or was refused saying why.

`patched()` makes every GPU worker the app builds (Hub: main, heavy, molmo, follow) a stand-in, so `App.build(...,
use_gpu=True)` builds the real app, every tool included, with no process started.
"""
from __future__ import annotations

import time
from contextlib import contextmanager

import numpy as np

from gpu_worker.loading import model_of


def _box(payload: dict) -> tuple[float, float, float, float]:
    if payload.get("box"):
        return tuple(float(v) for v in payload["box"])
    (x, y), = payload.get("points") or [[640.0, 360.0]]
    return float(x) - 20, float(y) - 20, float(x) + 20, float(y) + 20


def _square(x0, y0, x1, y1) -> list:
    return [[x0 + 2, y0 + 2], [x1 - 2, y0 + 2], [x1 - 2, y1 - 2], [x0 + 2, y1 - 2]]


# Six cells of the stand-in cell finder, in frame px (the leaf frame's eyepiece circle holds them): enough to group
# (group_cells takes at least four), of different sizes and shapes.
CELLS = ((600, 300, 660, 350), (700, 380, 780, 430), (820, 340, 880, 420), (520, 420, 560, 470), (640, 470, 720, 520),
         (760, 250, 800, 330))


class StandInWorker:
    """One GPU worker: every op answered at once; `calls` keeps (op, payload without bytes); `fail` makes a model (or
    an op) fail with that error; `resident` is what it holds, as the real client's is."""

    LABELS = {"micromind-gpu": "outline", "micromind-gpu-heavy": "analysis", "micromind-gpu-follow": "following",
              "micromind-gpu-molmo": "Molmo"}

    def __init__(self, name: str = "micromind-gpu", warm: bool = True, role: str | None = None) -> None:
        self.name, self.warm, self.role = name, warm, role
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, str] = {}
        self.ready, self.device, self.hung = True, "stand-in", ""
        self.on_problem: list = []
        self.loads = None
        self.loading: dict = {}
        self.resident: dict = {}
        self.children: dict = {}
        self.molmo_loaded = False

    # --- what GpuClient has ---------------------------------------------------------------------------------------
    async def start(self) -> None:
        self.ready = True

    async def ensure_started(self) -> "StandInWorker":
        return self

    def stop(self) -> None:
        pass

    def problem(self) -> str:
        return ""

    def processes(self) -> list:
        return []

    def wire(self) -> dict:
        return {"name": self.name, "role": self.role, "ready": True, "hung": "", "loading": {}, "resident": sorted(self.resident)}

    def watch(self) -> None:
        pass

    def unwatch(self) -> None:
        pass

    def check_hung(self, now=None) -> None:
        pass

    def models(self, op: str | None = None) -> list[str]:
        """The models asked for, in order (of one op when given)."""
        return [model_of(o, p) for o, p in self.calls if (op is None or o == op) and model_of(o, p)]

    async def call(self, op: str, timeout: float = 30.0, priority: str | None = None, **payload) -> dict:
        payload.pop("_quiet", None)
        self.calls.append((op, {k: v for k, v in payload.items() if k not in ("jpeg", "jpegs")} | {"priority": priority}))
        model = model_of(op, payload)
        why = self.fail.get(model or "") or self.fail.get(op)
        if why:
            raise RuntimeError(why)
        if op == "unload":
            return {"ok": True, "unloaded": self.resident.pop(payload.get("model"), None) is not None}
        if model and op not in ("ping",):
            self.resident.setdefault(model, {"sizeMb": 100.0, "seconds": 0.01, "usedAt": time.monotonic()})
        reply = getattr(self, "_" + op.replace("-", "_"), None)
        return {"ok": True, "ms": 1.0, **(reply({**payload, "_pictures": payload.get("jpegs")}) if reply is not None else {})}

    # --- each op's answer -------------------------------------------------------------------------------------------
    def _ping(self, payload):
        return {"device": "stand-in"}

    def _segment(self, payload):
        return {"polygons": [_square(*_box(payload))], "score": 0.9}

    def _cells(self, payload):
        import cv2

        from gpu_worker.worker import encode_labels

        labels = np.zeros((720, 1280), np.uint16)
        for n, (x0, y0, x1, y1) in enumerate(CELLS, start=1):
            cv2.ellipse(labels, ((x0 + x1) // 2, (y0 + y1) // 2), ((x1 - x0) // 2, (y1 - y0) // 2), 0, 0, 360, n, -1)
        return {"labels": encode_labels(labels), "scores": {str(n): 0.9 for n in range(1, len(CELLS) + 1)},
                "score": "cellprob", "polygons": [], "count": len(CELLS)}

    def _automask(self, payload):
        regions = []
        for x0, y0, x1, y1 in CELLS:
            bits = np.ones((y1 - y0 + 1, x1 - x0 + 1), bool)
            regions.append({"polygons": [_square(x0, y0, x1, y1)], "score": 0.9, "stability": 0.95,
                            "mask": {"bits": np.packbits(bits).tobytes(), "shape": list(bits.shape),
                                     "x0": float(x0), "y0": float(y0), "scale": 1.0}})
        return {"regions": regions, "prompts": 16, "candidates": 0}

    def _regions(self, payload):
        outline = (payload.get("polygons") or [[[600, 300], [700, 300], [700, 400], [600, 400]]])[0]
        xs, ys = [p[0] for p in outline], [p[1] for p in outline]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return {"regions": [{"polygons": [_square(x0, y0, mx, my)], "score": 0.9},
                            {"polygons": [_square(mx, my, x1, y1)], "score": 0.9}]}

    def _embed(self, payload):
        """One unit vector per picture, from its bytes (deterministic; alike pictures alike): enough to group."""
        import hashlib

        out = []
        for jpeg in payload.get("_pictures") or [b""]:
            v = np.frombuffer(hashlib.sha256(jpeg).digest()[:16], np.uint8).astype(float) + 1.0
            out.append((v / np.linalg.norm(v)).tolist())
        return {"vectors": out, "model": payload.get("model")}

    def _zeroshot(self, payload):
        names = list(payload.get("names") or ["thing"])
        return {"rankings": [[[name, round(1.0 / len(names), 3)] for name in names]]}

    def _track(self, payload):
        objects = {s["id"]: {"polygons": [_square(*s["box"])], "score": 0.9} for s in payload.get("start") or []}
        return {"objects": objects, "session": payload.get("session"), "seq": payload.get("seq")}

    def _molmo_load(self, payload):
        return {"loadMs": 10.0, "warmMs": 1.0, "memoryGb": 0.1}

    def _molmo_read(self, payload):
        return {}

    def _molmo_point(self, payload):
        return {"points": [[855.0, 390.0]], "text": "<point>", "read": 12}

    def _warmup(self, payload):
        return {}

    def _load(self, payload):
        return {}


def build_app(root):
    """The real app (App.build) over stand-in GPU workers, saving under `root`: every tool, no process."""
    from orchestrator.app import App
    from orchestrator.paths import Paths

    with patched():
        return App.build("http://127.0.0.1:1", Paths.under(root), use_gpu=True)


@contextmanager
def patched():
    """Every GPU worker the hub makes is a StandInWorker (orchestrator.hub.GpuClient), for as long as this lasts."""
    from orchestrator import hub as hub_module

    saved = hub_module.GpuClient
    hub_module.GpuClient = StandInWorker
    try:
        yield
    finally:
        hub_module.GpuClient = saved
