"""The public starter-example command: real files, no app, credentials or model.

Hashes are the pinned E3 fixture, generated independently from catalog commit
19f8bdf078dad6c5dd4ae50924ebec028aa58fca before implementing this command.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "three-particles" / "example.py"


def command(action, folder):
    return subprocess.run([sys.executable, str(EXAMPLE), action, str(folder)],
                          text=True, capture_output=True, timeout=10)


def test_newcomer_generates_the_exact_readable_e3_example(tmp_path):
    result = command("generate", tmp_path)
    assert result.returncode == 0, result.stderr
    data = (tmp_path / "particles-fixture.png").read_bytes()
    assert hashlib.sha256(data).hexdigest() == "af1b6c90a4dc0ca7daadd18301e916c9e2645fe75e40bb04c3f193c0505f055f"
    pixels = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    assert pixels.shape == (96, 96) and pixels.dtype == np.uint8
    assert hashlib.sha256(pixels.tobytes()).hexdigest() == "1343019a13d406a783dd80da0933f798c78b86d34886331e693ff92407767506"
    count, _, stats, _ = cv2.connectedComponentsWithStats((pixels == 40).astype(np.uint8), connectivity=8)
    assert count - 1 == 3
    assert sorted(stats[1:].tolist()) == [[15, 15, 21, 21, 317], [58, 53, 15, 15, 149], [70, 10, 16, 13, 208]]
    assert set(np.unique(pixels)) == {40, 220}


def test_generated_example_carries_its_recipe_and_pending_rights(tmp_path):
    result = command("generate", tmp_path)
    assert result.returncode == 0, result.stderr
    provenance = json.loads((tmp_path / "provenance.json").read_text())
    assert provenance["example"]["kind"] == "synthetic geometry"
    assert provenance["example"]["image"]["units"] == "pixels"
    assert provenance["example"]["image"]["physical_calibration"] is None
    assert provenance["example"]["source"]["commit"] == "19f8bdf078dad6c5dd4ae50924ebec028aa58fca"
    assert provenance["example"]["source"]["recorded_author"] == "Will Pulier"
    assert provenance["example"]["rights"]["publication_terms"] == "pending founder licence decision"
    assert provenance["example"]["rights"]["external_data"] == []
    assert set(provenance["generator"]["dependencies"]) == {"python", "numpy", "opencv-python-headless", "opencv"}
    assert len(provenance["generator"]["sha256"]) == 64


@pytest.mark.parametrize("damage", ["missing", "changed", "provenance", "invalid-record"])
def test_check_detects_damage_with_recovery_and_without_replacing_it(tmp_path, damage):
    assert command("generate", tmp_path).returncode == 0
    assert command("check", tmp_path).returncode == 0
    image = tmp_path / "particles-fixture.png"
    if damage == "missing":
        image.unlink()
    elif damage == "changed":
        image.write_bytes(b"not the original picture")
    elif damage == "provenance":
        (tmp_path / "provenance.json").write_text("{}")
    else:
        (tmp_path / "provenance.json").write_text("[]")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    result = command("check", tmp_path)
    assert result.returncode != 0
    assert "generate" in result.stderr and "new empty folder" in result.stderr
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_regeneration_preserves_an_existing_picture(tmp_path):
    image = tmp_path / "particles-fixture.png"
    image.write_bytes(b"a picture to preserve")
    result = command("generate", tmp_path)
    assert result.returncode != 0
    assert "new empty folder" in result.stderr
    assert image.read_bytes() == b"a picture to preserve"
