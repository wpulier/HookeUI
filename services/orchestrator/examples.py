"""Bundled, rights-cleared examples. Reads local bytes only; never fetches replacements."""
import hashlib
import json
from pathlib import Path

from .paths import EXAMPLES as ROOT  # the bundled examples' folder, decided in paths.py

# The bundled example as one library asset, the same for Library → Examples (library.py) and the walkthrough
# (onboarding.py): its copy's folder under the snapshots (a folder the camera bridge plays from), its key and its facts.
GUIDE = "human-mitosis-v1"
KEY = "onboarding:" + GUIDE
TITLE = "Human cells · David Root (CC0)"
SPECIMEN = "human cells"


def human_mitosis(*, preview: bool = False) -> Path:
    """Return the verified original TIFF (or lossless browser PNG), failing closed on damage."""
    metadata = json.loads((ROOT / 'metadata.json').read_text())
    item = metadata['preview' if preview else 'original']
    path = ROOT / item['file']
    if path.parent != ROOT or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
        raise ValueError('The included example is damaged. Reinstall HookeUI to restore it.')
    return path


def placed(snapshots: Path) -> tuple[Path, bytes]:
    """The verified preview copied to `snapshots/examples/<GUIDE>/` so the camera bridge can play it (a checkout moved or
    removed never strands it), written again when missing or different: (its path, its bytes)."""
    data = human_mitosis(preview=True).read_bytes()
    image = snapshots / "examples" / GUIDE / "human_mitosis.png"
    if not image.is_file() or image.read_bytes() != data:
        image.parent.mkdir(parents=True, exist_ok=True)
        temporary = image.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(image)
    return image, data
