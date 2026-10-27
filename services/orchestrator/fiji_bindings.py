"""Reviewed bindings of the managed Fiji (phase 3's E3, the review's correction 3): a Fiji function the app can run, with
its input, its output and its headless route declared, proven by running it.

**Analyze Particles** (`fiji-analyze-particles`, catalog/models/fiji-analyze-particles):
- **Input:** the camera's picture, made 8-bit by ImageJ itself (`8-bit`), passed explicitly (never WindowManager).
- **Threshold:** ImageJ's `Default` method for dark things on a light field (brightfield), or one given.
- **Output:** native ParticleAnalyzer mask pixels as a uint16 label map, with native area, pixel-center centroid and
  mean on its 8-bit input. Polygons remain drawing/reference data; population creation preserves the labels exactly.
- **What it is:** particles above a threshold, not cells: it joins "find cells" only as an option the user picks, said
  as particles (options.CELLS "fiji-analyze-particles").
- **Its proof** (`proof`, run on every new Fiji generation before it is used, fiji_generations.PROOFS): the binding and
  the same steps as an ImageJ macro and an independent native count-mask process, on generated inputs with known
  pixels (three particles, edge/asymmetry/hole, zero). Activation requires the current mask contract and script hashes.

The worker (`services/fiji_worker/server.groovy`) is one headless Fiji of the generation in use, started on first use,
kept, and replaced when the generation changes; nothing runs on the event loop.
"""
from __future__ import annotations

import json
import base64
import zlib
import hashlib
import math
import subprocess
import tempfile
import threading
from pathlib import Path

from . import fiji_generations as generations
from . import owned_process

SERVER = Path(__file__).resolve().parent.parent / "fiji_worker" / "server.groovy"
REFERENCE = SERVER.with_name("reference_particles.groovy")
BINDING = "fiji-analyze-particles"
MASK_CONTRACT = "imagej-particle-pixels-v1"
MIN_SIZE = 20.0  # px²: smaller is noise on the camera's picture
MACRO = '''open("{image}");
run("8-bit");
setAutoThreshold("Default");
run("Analyze Particles...", "size={min}-Infinity show=Overlay display clear");
print("n " + nResults);
for (i = 0; i < Overlay.size; i++) {{
  Overlay.activateSelection(i);
  getSelectionCoordinates(xs, ys);
  s = "";
  for (j = 0; j < xs.length; j++) s = s + xs[j] + "," + ys[j] + ";";
  print("p " + s);
}}
print("done particles");
'''


class Worker:
    """One headless Fiji of a generation, speaking server.groovy's lines."""

    def __init__(self, script: Path = SERVER) -> None:
        self.script = script  # server.groovy (this binding), or functions.groovy (fiji_runner.py: function folders)
        self._process: subprocess.Popen | None = None
        self._generation: tuple | None = None
        self._lock = threading.Lock()

    def _start(self, n: int) -> subprocess.Popen:
        fiji = generations.fiji_dir(n)
        stat = fiji.stat()
        identity = (str(fiji.resolve()), n, stat.st_dev, stat.st_ino, stat.st_ctime_ns)
        if self._process is not None and self._process.poll() is None and self._generation == identity:
            return self._process
        self.stop()
        fiji = generations.fiji_dir(n)
        self._process = subprocess.Popen([str(fiji / "fiji"), "--headless", "--console", "--run", str(self.script)],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         cwd=str(fiji), text=True, start_new_session=True,
                                         env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home())})  # its launcher sizes the heap with sysctl
        self._generation = identity
        with owned_process.watching(self._process, generations.entry().load_limit_s or 120, "Fiji's startup"):
            for line in self._process.stdout:  # its first answer says it is ready
                if line.startswith("{"):
                    if not json.loads(line).get("ok"):
                        raise generations.Failed("Fiji's worker didn't start")
                    break
            else:
                raise generations.Failed("Fiji's worker stopped before it was ready")
        return self._process

    def call(self, request: dict, n: int | None = None) -> dict:
        n = generations.current() if n is None else n
        if n is None:
            raise generations.Failed("Fiji isn't set up here yet: its card on the Models page sets it up")
        try:
            with owned_process.locked(self._lock):
                try:
                    process = self._start(n)
                    with owned_process.watching(process, generations.STEP_S, "Fiji's run"):
                        process.stdin.write(json.dumps(request) + "\n")
                        process.stdin.flush()
                        for line in process.stdout:
                            if line.startswith("@@"):
                                reply = json.loads(line[2:])
                                break
                        else:
                            raise generations.Failed("Fiji's worker stopped")
                except (owned_process.Stopped, generations.Failed, OSError, ValueError) as exc:
                    self.stop()
                    raise generations.Failed(str(exc)) from exc
        except owned_process.Stopped as exc:
            raise generations.Failed(str(exc)) from exc
        if not isinstance(reply, dict):
            raise generations.Failed("Fiji's worker returned an invalid result")
        if not reply.get("ok"):
            raise generations.Failed(f"Fiji: {reply.get('error')}")
        return reply

    def stop(self) -> None:
        if self._process is not None:
            owned_process.terminate(self._process)
        self._process = None


WORKER = Worker()


def analyze_particles(image: Path, *, min_size: float = MIN_SIZE, n: int | None = None, worker: Worker = WORKER) -> dict:
    """Complete native label pixels plus per-label measurements, drawing polygons, threshold and pinned generation."""
    n = generations.current() if n is None else n
    if n is None:
        raise generations.Failed("Fiji has no active generation")
    out = worker.call({"op": "particles", "image": str(image), "min_size": min_size}, n)
    if not isinstance(out, dict) or out.get("ok") is not True or not isinstance(out.get("particles"), list):
        raise generations.Failed("Fiji returned no complete particle result")
    width, height = out.get("width"), out.get("height")
    if type(width) is not int or type(height) is not int or min(width, height) <= 0:
        raise generations.Failed("Fiji returned invalid image dimensions")
    threshold = out.get("threshold")
    if not isinstance(threshold, list) or len(threshold) != 2 or not all(_number(v) for v in threshold):
        raise generations.Failed("Fiji returned no valid threshold")
    for particle in out["particles"]:
        if not isinstance(particle, dict) or not _polygon(particle.get("polygon")):
            raise generations.Failed("Fiji returned an invalid particle polygon")
        if any(not (0 <= x <= width and 0 <= y <= height) for x, y in particle["polygon"]):
            raise generations.Failed("Fiji returned a particle outside its input image")
        if any(not _number(particle.get(k)) for k in ("area", "cx", "cy")):
            raise generations.Failed("Fiji returned invalid particle measurements")
    labels = native_labels(out)
    import cv2
    import numpy as np

    source = cv2.imread(str(image), cv2.IMREAD_UNCHANGED)
    if source is None or source.shape[:2] != labels.shape:
        raise generations.Failed("Fiji returned masks on a different input grid")
    if source.ndim == 2 and source.dtype == np.uint8:
        counts = np.bincount(labels.ravel(), minlength=len(out["particles"]) + 1)
        sums = np.bincount(labels.ravel(), weights=source.ravel(), minlength=len(counts))
        if any(abs(p["mean"] - sums[p["label"]] / counts[p["label"]]) > 1e-6 for p in out["particles"]):
            raise generations.Failed("Fiji native mean disagrees with original input pixels")
    from .evidence import encode_labels

    return {**out, "labels": encode_labels(labels), "generation": n}


def native_labels(out: dict):
    """Decode only the complete native pixel contract; never fall back to a drawing polygon."""
    import numpy as np

    try:
        if out.get("maskContract") != MASK_CONTRACT:
            raise ValueError("missing current native mask contract")
        width, height = out["width"], out["height"]
        if width * height > 16_000_000:
            raise ValueError("image grid is too large")
        wire = out["labels"]
        if wire["dtype"] != "<u2" or wire["shape"] != [height, width]:
            raise ValueError("label dtype or grid differs from input")
        encoded = wire["base64"]
        if not isinstance(encoded, str) or len(encoded) > width * height * 3 + 1024:
            raise ValueError("invalid encoded mask length")
        stream = zlib.decompressobj()
        raw = stream.decompress(base64.b64decode(encoded, validate=True), width * height * 2 + 1)
        if len(raw) != width * height * 2 or not stream.eof or stream.unused_data or stream.unconsumed_tail:
            raise ValueError("truncated, trailing or oversized mask data")
        labels = np.frombuffer(raw, dtype="<u2").reshape(height, width)
        particles = out["particles"]
        if len(particles) > 65535:
            raise ValueError("too many particles")
        if any(type(p.get("label")) is not int for p in particles) or [p.get("label") for p in particles] != list(range(1, len(particles) + 1)):
            raise ValueError("invalid particle label identities")
        if set(np.unique(labels)) - {0} != set(range(1, len(particles) + 1)):
            raise ValueError("missing or unknown particle pixels")
        counts = np.bincount(labels.ravel(), minlength=len(particles) + 1)
        sx = np.bincount(labels.ravel(), weights=np.tile(np.arange(width, dtype=float) + .5, height), minlength=len(counts))
        sy = np.bincount(labels.ravel(), weights=np.repeat(np.arange(height, dtype=float) + .5, width), minlength=len(counts))
        for p in particles:
            label = p["label"]
            if any(not _number(p.get(k)) for k in ("area", "cx", "cy", "mean")):
                raise ValueError("invalid native statistics")
            if (p["area"] != int(counts[label]) or abs(p["cx"] - sx[label] / counts[label]) > 1e-6
                    or abs(p["cy"] - sy[label] / counts[label]) > 1e-6 or not 0 <= p["mean"] <= 255):
                raise ValueError("native statistics disagree with particle pixels")
        return labels
    except (KeyError, TypeError, ValueError, zlib.error) as exc:
        raise generations.Failed(f"Fiji returned invalid native particle masks ({exc})") from None


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _polygon(value) -> bool:
    return (isinstance(value, list) and len(value) >= 3
            and all(isinstance(p, (list, tuple)) and len(p) == 2 and all(_number(v) for v in p) for p in value))


def compare(image: Path, result: dict, *, min_size: float = MIN_SIZE) -> dict:
    """Reference agreement on this exact input, in a separate process of the producing generation."""
    import numpy as np
    from .evidence import decode_labels

    n = result["generation"]
    reference = by_reference(image, n, min_size=min_size)
    ours, theirs_pixels = decode_labels(result["labels"]), reference["labels"]
    pixel_agreement = np.array_equal(ours, theirs_pixels)
    measurements_agree = len(result["particles"]) == len(reference["particles"]) and all(
        a["label"] == b["label"] and a["area"] == b["area"]
        and all(abs(a[k] - b[k]) <= 1e-6 for k in ("cx", "cy", "mean"))
        for a, b in zip(result["particles"], reference["particles"]))
    mine = [[[round(x, 3), round(y, 3)] for x, y in p["polygon"]] for p in result["particles"]]
    theirs = [[[round(x, 3), round(y, 3)] for x, y in p] for p in by_macro(image, n, min_size=min_size)]
    return {"reference": "ImageJ Analyze Particles macro", "generation": n, "separateProcess": True,
            "inputSha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "macroSha256": hashlib.sha256(MACRO.encode()).hexdigest(), "decimalPlaces": 3,
            "agreement": mine == theirs and pixel_agreement and measurements_agree,
            "pixelAgreement": pixel_agreement, "measurementAgreement": measurements_agree,
            "maskContract": MASK_CONTRACT, "referenceMaskMethod": "Unmodified ImageJ ParticleAnalyzer count masks, private ResultsTable",
            "referenceSha256": hashlib.sha256(REFERENCE.read_bytes()).hexdigest(),
            "labelsSha256": hashlib.sha256(ours.astype("<u2").tobytes()).hexdigest(),
            "referenceLabelsSha256": hashlib.sha256(theirs_pixels.astype("<u2").tobytes()).hexdigest(),
            "referenceMeasurements": reference["particles"], "measurementTolerance": 1e-6,
            "count": len(mine), "referenceCount": len(theirs),
            "bindingPolygons": mine, "referencePolygons": theirs,
            "meaning": "Upstream agreement on this input; not an independent accuracy assessment"}


def by_reference(image: Path, n: int, *, min_size: float = MIN_SIZE) -> dict:
    """Separate Fiji process, unmodified count-mask output and a private native measurement table."""
    import cv2
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        # Paths are app-created, not user-entered macro text. Refuse delimiters rather than escaping script arguments.
        if any(c in str(image) + tmp for c in "'\\\n\r"):
            raise generations.Failed("Reference input path cannot be represented safely")
        generations.run_fiji(n, ["--console", "--run", str(REFERENCE),
            f"imagePath='{image}',outputDir='{tmp}',minSize={min_size}"], what="Analyze Particles count-mask reference")
        try:
            result = json.loads((Path(tmp) / "reference.json").read_text())
            labels = cv2.imread(str(Path(tmp) / "reference-labels.tif"), cv2.IMREAD_UNCHANGED)
            if result.get("ok") is not True or labels is None or labels.dtype != np.uint16:
                raise ValueError("reference produced no complete uint16 masks")
            result.update(maskContract=MASK_CONTRACT, labels={"dtype": "<u2", "shape": list(labels.shape),
                "base64": base64.b64encode(zlib.compress(labels.astype("<u2").tobytes())).decode()})
            result["labels"] = native_labels(result)
            return result
        except (OSError, ValueError, TypeError) as exc:
            raise generations.Failed(f"ImageJ's count-mask reference failed ({exc})") from None


def by_macro(image: Path, n: int, *, min_size: float = MIN_SIZE) -> list[list[tuple[float, float]]]:
    """The same steps as an ImageJ macro, in a Fiji of its own: its particles' polygons, in its order."""
    with tempfile.TemporaryDirectory() as tmp:
        macro = Path(tmp) / "particles.ijm"
        macro.write_text(MACRO.format(image=str(image), min=min_size))
        out = generations.run_fiji(n, ["--console", "-macro", str(macro)], what="Analyze Particles as a macro")
    polygons, count, complete = [], None, False
    try:
        for line in out.splitlines():
            if line.startswith("n "):
                if count is not None or complete:
                    raise ValueError("duplicate count")
                count = int(line[2:].strip())
            elif line.startswith("p "):
                if count is None or complete:
                    raise ValueError("polygon outside result")
                pts = [tuple(float(v) for v in p.split(",")) for p in line[2:].strip().strip(";").split(";") if p]
                if not _polygon(pts):
                    raise ValueError("invalid polygon")
                polygons.append(pts)
            elif line == "done particles":
                if complete or count is None or count != len(polygons):
                    raise ValueError("invalid completed result")
                complete = True
        if not complete or count != len(polygons):
            raise ValueError("missing result completion or count")
    except (ValueError, TypeError) as exc:
        raise generations.Failed(f"ImageJ's reference macro returned an incomplete or invalid result ({exc})") from None
    return polygons


def proof_identity() -> dict:
    return {"contract": MASK_CONTRACT, "bindingSha256": hashlib.sha256(SERVER.read_bytes()).hexdigest(),
            "referenceSha256": hashlib.sha256(REFERENCE.read_bytes()).hexdigest()}


def proof_current(proof) -> bool:
    return isinstance(proof, dict) and proof.get("passed") is True and all(proof.get(k) == v for k, v in proof_identity().items())


def fixture(folder: Path) -> Path:
    """A drawn picture with three known dark particles on a light field (a circle of radius 10, one of 7, a 16x13 box)."""
    import cv2
    import numpy as np

    picture = np.full((96, 96), 220, np.uint8)
    cv2.circle(picture, (25, 25), 10, 40, -1)
    cv2.circle(picture, (65, 60), 7, 40, -1)
    cv2.rectangle(picture, (70, 10), (85, 22), 40, -1)
    path = folder / "particles-fixture.png"
    cv2.imwrite(str(path), picture)
    return path


def proof(fiji_root: Path) -> dict:
    """Public generated inputs with known occupied pixels, plus an independent Fiji reference. Never repairs old receipts."""
    import cv2
    import numpy as np
    from .evidence import decode_labels

    n = int(fiji_root.parent.name)
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        picture = fixture(folder)
        expected = np.zeros((96, 96), np.uint16)
        expected[10:23, 70:86] = 1
        cv2.circle(expected, (25, 25), 10, 2, -1)
        cv2.circle(expected, (65, 60), 7, 3, -1)
        examples = [(picture, expected, [(208, 78, 16.5, 40), (317, 25.5, 25.5, 40), (149, 65.5, 60.5, 40)])]
        # Edge-touching asymmetric L and a ring: 44/84 pixels, hole left empty, nonuniform intensities.
        pixels = np.full((96, 96), 240, np.uint8)
        pixels[0:4, 0:8] = 0
        pixels[4:8, 0:3] = 10
        pixels[20:30, 20:30] = 40
        pixels[23:27, 23:27] = 240
        expected = np.zeros((96, 96), np.uint16)
        expected[0:4, 0:8] = expected[4:8, 0:3] = 1
        expected[20:30, 20:30] = 2
        expected[23:27, 23:27] = 0
        picture = folder / "edge-hole-asymmetric.png"
        cv2.imwrite(str(picture), pixels)
        examples.append((picture, expected, [(44, 146/44, 136/44, 120/44), (84, 25, 25, 40)]))
        picture = folder / "zero.png"
        cv2.imwrite(str(picture), np.full((96, 96), 255, np.uint8))
        examples.append((picture, np.zeros((96, 96), np.uint16), []))
        worker = Worker()
        try:
            for picture, expected, metrics in examples:
                result = analyze_particles(picture, n=n, worker=worker)
                comparison = compare(picture, result)
                if not comparison["agreement"] or not np.array_equal(decode_labels(result["labels"]), expected):
                    raise generations.Failed(f"Analyze Particles native pixels differ on {picture.name}")
                if len(result["particles"]) != len(metrics) or any(
                    p["area"] != metric[0] or any(abs(p[k] - value) > 1e-6 for k, value in zip(("cx", "cy", "mean"), metric[1:]))
                    for p, metric in zip(result["particles"], metrics)):
                    raise generations.Failed(f"Analyze Particles native measurements differ on {picture.name}")
                cases.append({"input": picture.name, "inputSha256": hashlib.sha256(picture.read_bytes()).hexdigest(),
                              "comparison": comparison, "expectedMeasurements": metrics})
        finally:
            worker.stop()
    return {**proof_identity(), "passed": True, "cases": cases,
            "summary": "Exact native masks and measurements match generated truth and independent Fiji (three particles, edge/asymmetry/hole, zero)"}


generations.PROOFS[BINDING] = proof
