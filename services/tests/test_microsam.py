"""micro-sam as a cell finder (CELLS "micro-sam-lm") and a click outline model (OUTLINE "micro-sam-lm"):
it runs in its own Python (noncommercial/microsam_server.py), reached only through the licence
router; here a stand-in server speaks the same wire, so nothing needs micro-sam installed."""
import io
import sys
import textwrap
from pathlib import Path

import cv2
import numpy as np
import pytest

from gpu_worker import worker
from gpu_worker.worker import OP_COMPONENTS, licence_refusal
from noncommercial import microsam_cells
from noncommercial.microsam_cells import MicroSamCells, best_mask, labels_to_polygons, unpack_masks
from noncommercial.microsam_server import read_message, write_message
from orchestrator import licences
from orchestrator.options import CELLS, OUTLINE, Settings, resolve


@pytest.fixture
def research(monkeypatch):
    monkeypatch.setenv("MICROMIND_EDITION", "research")


def jpeg(w=64, h=48) -> bytes:
    return cv2.imencode(".jpg", np.zeros((h, w, 3), np.uint8))[1].tobytes()


def test_the_wire_carries_a_header_and_a_payload_both_ways():
    stream = io.BytesIO()
    write_message(stream, {"op": "cells", "crop": [1, 2, 3, 4]}, b"\xff\xd8jpeg")
    write_message(stream, {"op": "ping"})
    stream.seek(0)
    assert read_message(stream) == ({"op": "cells", "crop": [1, 2, 3, 4]}, b"\xff\xd8jpeg")
    assert read_message(stream) == ({"op": "ping"}, b"")
    with pytest.raises(EOFError):
        read_message(stream)


def test_each_labelled_cell_becomes_one_polygon_in_frame_pixels():
    labels = np.zeros((60, 80), np.uint16)
    labels[5:15, 5:15] = 1
    labels[20:40, 30:50] = 7  # labels need not be consecutive
    labels[50:52, 70:72] = 3  # a speck under the minimum area
    polygons = labels_to_polygons(labels, offset=(100, 200))
    assert len(polygons) == 2
    boxes = sorted((min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly))
                   for poly in polygons)
    assert boxes == [(105, 205, 114, 214), (130, 220, 149, 239)]
    assert labels_to_polygons(np.zeros((10, 10), np.uint16)) == []


def test_a_click_takes_the_best_mask_within_the_area_limit():
    small = np.zeros((100, 100), bool)
    small[40:50, 40:50] = True  # 100 px
    medium = np.zeros((100, 100), bool)
    medium[35:55, 35:55] = True  # 400 px
    field = np.ones((100, 100), bool)  # the whole field: never what a click means
    packed = small.astype(np.uint8) | (medium.astype(np.uint8) << 1) | (field.astype(np.uint8) << 2)
    masks = unpack_masks(packed, 3)
    assert [int(m.sum()) for m in masks] == [100, 400, 10000]
    assert best_mask(masks, [0.80, 0.85, 0.99], {}) == 1
    assert best_mask(masks, [0.80, 0.85, 0.99], {"prefer_small": True}) == 0
    assert best_mask([field], [0.99], {}) is None


STAND_IN = textwrap.dedent('''
    import sys
    sys.path.insert(0, SERVICES)
    import cv2
    import numpy as np
    from noncommercial.microsam_server import read_message, write_message
    out = sys.stdout.buffer
    while True:
        try:
            header, payload = read_message(sys.stdin.buffer)
        except EOFError:
            break
        if header["op"] == "cells":
            x, y, w, h = header["crop"]
            labels = np.zeros((h, w), np.uint16)
            labels[2:12, 2:12] = 1
            labels[20:30, 20:34] = 2
            write_message(out, {"ok": True, "count": 2, "ms": 5.0, "shape": [h, w], "mode": header["mode"]},
                          cv2.imencode(".png", labels)[1].tobytes())
        elif header["op"] == "segment":
            packed = np.zeros((48, 64), np.uint8)
            packed[10:20, 10:20] = 1
            packed[5:40, 5:60] |= 2
            write_message(out, {"ok": True, "scores": [0.9, 0.7], "ms": 3.0, "shape": [48, 64]},
                          cv2.imencode(".png", packed)[1].tobytes())
        else:
            write_message(out, {"ok": False, "error": "no such op"})
''')


@pytest.fixture
def stand_in(tmp_path):
    script = tmp_path / "stand_in.py"
    script.write_text(STAND_IN.replace("SERVICES", repr(str(Path(microsam_cells.__file__).parents[1]))))
    finder = MicroSamCells(command=[sys.executable, str(script)])
    yield finder
    finder.close()


def test_cells_come_back_as_polygons_offset_by_the_crop(stand_in):
    out = stand_in.outline({"jpeg": jpeg(), "crop": [10, 5, 40, 32], "model": "micro-sam-lm"})
    assert out["count"] == 2 and out["modelMs"] == 5.0
    xs = sorted(min(p[0] for p in poly) for poly in out["polygons"])
    assert xs == [12, 30]  # 2 + 10 and 20 + 10: frame pixels, not the crop's


def test_a_click_comes_back_as_its_best_mask(stand_in):
    out = stand_in.segment({"frame_id": 3, "jpeg": jpeg(), "points": [[15, 15]], "model": "micro-sam-lm"})
    assert out["score"] == 0.9 and out["area"] == 100
    # Asked for nothing it can do, the server's error is the caller's error.
    with pytest.raises(RuntimeError, match="micro-sam: no such op"):
        stand_in.call({"op": "nothing"})


def test_one_process_serves_every_call_and_a_dead_one_is_restarted(stand_in):
    stand_in.outline({"jpeg": jpeg(), "crop": [0, 0, 40, 32]})
    first = stand_in._process
    stand_in.outline({"jpeg": jpeg(), "crop": [0, 0, 40, 32]})
    assert stand_in._process is first
    first.kill()
    first.wait()
    assert stand_in.outline({"jpeg": jpeg(), "crop": [0, 0, 40, 32]})["count"] == 2  # started again
    assert stand_in._process is not first


def test_a_server_that_dies_mid_call_is_an_error_not_a_hang(tmp_path):
    script = tmp_path / "dies.py"
    script.write_text("import sys\nsys.stdin.buffer.read(4)\nsys.exit(1)\n")
    finder = MicroSamCells(command=[sys.executable, str(script)])
    with pytest.raises(RuntimeError, match="micro-sam stopped"):
        finder.outline({"jpeg": jpeg(), "crop": [0, 0, 40, 32]})
    assert finder._process is None


def test_without_its_python_it_says_it_is_not_installed(monkeypatch, tmp_path):
    monkeypatch.setenv("MICROMIND_MICROSAM_PYTHON", str(tmp_path / "missing" / "python"))
    with pytest.raises(RuntimeError, match="micro-sam is not installed here"):
        MicroSamCells().outline({"jpeg": jpeg()})


def test_micro_sam_is_non_commercial_like_cellpose(monkeypatch):
    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    assert licences.COMPONENTS["micro-sam-lm"].tier == "noncommercial"
    assert "non-commercial" in licences.COMPONENTS["micro-sam-lm"].licence
    assert CELLS["micro-sam-lm"].tier == "noncommercial" and OUTLINE["micro-sam-lm"].tier == "noncommercial"
    # So the commercial edition's cell finder is EdgeTAM's automatic masks (open), and it says why.
    assert Settings().cells == "edgetam-automask"
    assert resolve("cells", "micro-sam-lm")[0] == "edgetam-automask"
    settings = Settings()
    assert settings.update({"outline": "micro-sam-lm"}) and settings.outline == "edgetam"


def test_the_gpu_worker_refuses_micro_sam_in_the_commercial_edition(monkeypatch):
    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    assert OP_COMPONENTS["cells"]({"model": "micro-sam-lm"}) == ("micro-sam-lm",)
    assert OP_COMPONENTS["cells"]({}) == ("cellpose-sam",)
    assert licence_refusal({"op": "cells", "model": "micro-sam-lm"})["licence"] is True
    assert licence_refusal({"op": "segment", "model": "micro-sam-lm"})["licence"] is True
    monkeypatch.setenv("MICROMIND_EDITION", "research")
    assert licence_refusal({"op": "cells", "model": "micro-sam-lm"}) is None
    assert licence_refusal({"op": "segment", "model": "micro-sam-lm"}) is None


def test_the_worker_runs_the_cell_finder_the_option_names(research, monkeypatch):
    made = []

    class Finder:
        def __init__(self, device):
            made.append(self)

        def outline(self, req):
            return {"polygons": [], "count": 0, "ms": 1.0, "who": req.get("model") or "default"}

        def segment(self, req):
            return {"polygons": [], "score": 0.5, "area": 0, "ms": 1.0}

    module = type(sys)("stand_in_finders")
    module.CellposeCells = module.MicroSamCells = Finder
    monkeypatch.setattr(licences, "adapter", lambda component: module)
    models = worker.Models.__new__(worker.Models)
    models._cells, models.device = {}, "cpu"
    assert models.cells({"model": "micro-sam-lm"})["who"] == "micro-sam-lm"
    assert models.cells({})["who"] == "default"  # Cellpose-SAM, as before
    assert models.segment({"model": "micro-sam-lm", "jpeg": b""})["score"] == 0.5
    assert len(made) == 2  # one per finder, kept: the click reused micro-sam's
    with pytest.raises(ValueError):
        models.cells({"model": "no-such-finder"})


def test_the_default_python_is_the_separate_environment(monkeypatch):
    monkeypatch.delenv("MICROMIND_MICROSAM_PYTHON", raising=False)
    assert microsam_cells.python().parts[-4:] == ("venvs", "microsam", "bin", "python")
