"""Bundled, rights-cleared examples. Reads local bytes only; never fetches replacements."""
import hashlib
import json
from pathlib import Path

from .paths import EXAMPLES as ROOT  # the bundled examples' folder, decided in paths.py


def human_mitosis(*, preview: bool = False) -> Path:
    """Return the verified original TIFF (or lossless browser PNG), failing closed on damage."""
    metadata = json.loads((ROOT / 'metadata.json').read_text())
    item = metadata['preview' if preview else 'original']
    path = ROOT / item['file']
    if path.parent != ROOT or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
        raise ValueError('The included example is damaged. Reinstall ObservationUI to restore it.')
    return path
