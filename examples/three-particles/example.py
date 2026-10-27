"""Generate the synthetic geometry used by the first scientific journey."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
import tempfile
from pathlib import Path


def fixture(folder: Path) -> Path:
    """The unchanged drawing recipe from the pinned E3 Fiji binding."""
    import cv2
    import numpy as np

    picture = np.full((96, 96), 220, np.uint8)
    cv2.circle(picture, (25, 25), 10, 40, -1)
    cv2.circle(picture, (65, 60), 7, 40, -1)
    cv2.rectangle(picture, (70, 10), (85, 22), 40, -1)
    path = folder / "particles-fixture.png"
    if not cv2.imwrite(str(path), picture):
        raise OSError(f"Could not write {path}")
    return path


def check_image(folder: Path, manifest: dict) -> Path:
    import cv2
    import numpy as np

    expected = manifest["image"]
    image = folder / expected["file"]
    data = image.read_bytes()
    if len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
        raise ValueError(f"{image.name} differs from the pinned example")
    pixels = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if pixels is None or pixels.shape != (96, 96) or pixels.dtype != np.uint8:
        raise ValueError(f"{image.name} is not a 96 × 96 grayscale uint8 image")
    if hashlib.sha256(pixels.tobytes()).hexdigest() != expected["pixel_sha256"]:
        raise ValueError(f"{image.name} has changed pixels")
    return image


def generate(folder: Path, manifest: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    if any(folder.iterdir()):
        raise ValueError(f"{folder} is not empty; generation never replaces existing files")
    with tempfile.TemporaryDirectory() as staging:
        fixture(Path(staging))
        data = check_image(Path(staging), manifest).read_bytes()
    import cv2
    import numpy as np

    provenance = {
        "example": manifest,
        "generator": {
            "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "dependencies": {"python": platform.python_version(), "numpy": np.__version__,
                             "opencv-python-headless": importlib.metadata.version("opencv-python-headless"),
                             "opencv": cv2.__version__},
        },
    }
    image = folder / manifest["image"]["file"]
    with image.open("xb") as output:
        output.write(data)
    with (folder / "provenance.json").open("x") as output:
        output.write(json.dumps(provenance, indent=2) + "\n")
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["generate", "check"])
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    try:
        manifest = json.loads(Path(__file__).with_name("manifest.json").read_text())
        if args.action == "generate":
            image = generate(args.folder, manifest)
        else:
            image = check_image(args.folder, manifest)
            provenance = json.loads((args.folder / "provenance.json").read_text())
            if not isinstance(provenance, dict) or provenance.get("example") != manifest:
                raise ValueError("provenance.json is missing the example's original recipe and generator record")
            generator = provenance.get("generator")
            if not isinstance(generator, dict) or not generator.get("dependencies") or not generator.get("sha256"):
                raise ValueError("provenance.json is missing its generator record")
        print(f"Verified synthetic example: {image}")
        return 0
    except (OSError, ValueError, ImportError) as error:
        print(f"Example unavailable: {error}. Restore this checkout's example files and locked dependencies, "
              "then run 'uv run --locked --project services python examples/three-particles/example.py "
              "generate <new empty folder>'. Keep any changed original for inspection.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
