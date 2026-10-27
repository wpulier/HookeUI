"""The public three-particle recipe, shared by setup checks and the shelf's example.

This is a synthetic mechanical example, never a scientific qualification. Original snapshots and proof inputs are
not overwritten: an existing example must still match its pinned recipe before it can be reused.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import threading

_lock = threading.Lock()
TITLE = 'Synthetic three particles — functional example, not scientific qualification'
BOX = (15.0, 15.0, 35.0, 35.0)
DISC = (48.0, 48.0, 48.0)


def picture(paths) -> Path:
    folder = paths.snapshots / 'public-examples' / 'three-particles-v1'
    source = Path(__file__).resolve().parents[2] / 'examples' / 'three-particles' / 'example.py'
    spec = importlib.util.spec_from_file_location('observationui_public_example', source)
    if spec is None or spec.loader is None:
        raise OSError('the public example recipe is missing from this checkout')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = json.loads(source.with_name('manifest.json').read_text())
    with _lock:
        if folder.is_dir():
            return module.check_image(folder, manifest)
        return module.generate(folder, manifest)
