"""EdgeTAM's streaming session (gpu_worker/objects.py) with more than one object at once.

On 2026-10-06, 363 `object-follow` steps failed with "maskmem_features in conditioning outputs
cannot be empty when not is_initial_conditioning_frame": every time two or more objects were
prompted on the same frame (several cells started at once, a stop leaving two or more, the
reseed), and then on every step after until all of them were stopped. These run the real model on
drawn frames, so they need EdgeTAM's weights on this machine.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")
if not hasattr(transformers, "EdgeTamVideoModel"):
    pytest.skip("this transformers has no EdgeTAM video model", allow_module_level=True)
import os

from gpu_worker import manifests, model_files, objects

# This Mac's own files for EdgeTAM (the models folder, else an old cache at the pinned revision), not the stand-in
# every other test counts as downloaded (conftest MICROMIND_MODEL_FILES=standin).
REAL = {k: v for k, v in os.environ.items() if k not in ("MICROMIND_MODEL_FILES", "MICROMIND_CATALOG")}
if model_files.location(manifests.get("edgetam-video"), REAL)[0] is None:
    pytest.skip("EdgeTAM's weights are not on this machine", allow_module_level=True)

# Three dark cells on a light field; each moves a little every frame.
CELLS = {"a": ((200, 200), (3, 0)), "b": ((420, 300), (0, -2)), "c": ((320, 120), (-2, 2))}


def frame(t: int) -> bytes:
    img = np.full((480, 640, 3), 200, np.uint8)
    for (x, y), (vx, vy) in CELLS.values():
        cv2.ellipse(img, (x + vx * t, y + vy * t), (20, 14), 0, 0, 360, (40, 40, 40), -1, cv2.LINE_AA)
    return cv2.imencode(".jpg", img)[1].tobytes()


def box(oid: str, t: int = 0) -> list[float]:
    (x, y), (vx, vy) = CELLS[oid]
    cx, cy = x + vx * t, y + vy * t
    return [cx - 24, cy - 18, cx + 24, cy + 18]


def on_target(seen: dict, oid: str, t: int) -> bool:
    (x, y), (vx, vy) = CELLS[oid]
    x0, y0, x1, y1 = seen["box"]
    return seen["present"] and abs((x0 + x1) / 2 - (x + vx * t)) < 8 and abs((y0 + y1) / 2 - (y + vy * t)) < 8


@pytest.fixture(scope="module")
def loaded():
    with pytest.MonkeyPatch.context() as mp:
        for name in ("MICROMIND_MODEL_FILES", "MICROMIND_CATALOG"):
            mp.delenv(name, raising=False)
        return objects.ObjectSession()


@pytest.fixture
def session(loaded):
    loaded.session, loaded.frame_idx, loaded.ids, loaded.boxes = None, 0, {}, {}
    return loaded


def test_two_objects_started_on_one_frame_are_both_followed(session):
    out = session.step(frame(0), start=[{"id": "a", "box": box("a")}, {"id": "b", "box": box("b")}])
    assert set(out["objects"]) == {"a", "b"}
    for t in range(1, 4):
        out = session.step(frame(t))
        assert all(on_target(out["objects"][oid], oid, t) for oid in ("a", "b")), out["objects"]


def test_stopping_one_of_three_keeps_following_the_other_two(session):
    session.step(frame(0), start=[{"id": "a", "box": box("a")}])
    session.step(frame(1), start=[{"id": "b", "box": box("b", 1)}])
    session.step(frame(2), start=[{"id": "c", "box": box("c", 2)}])
    out = session.step(frame(3), stop=["c"])  # a new session, prompted with a's and b's last boxes
    assert set(out["objects"]) == {"a", "b"}
    out = session.step(frame(4))
    assert all(on_target(out["objects"][oid], oid, 4) for oid in ("a", "b")), out["objects"]


def test_the_reseed_keeps_following_every_object(session, monkeypatch):
    monkeypatch.setattr(objects, "RESEED_FRAMES", 2)
    session.step(frame(0), start=[{"id": "a", "box": box("a")}])
    session.step(frame(1), start=[{"id": "b", "box": box("b", 1)}])
    for t in range(2, 6):  # reseeds at 2 and 4
        out = session.step(frame(t))
        assert all(on_target(out["objects"][oid], oid, t) for oid in ("a", "b")), (t, out["objects"])

