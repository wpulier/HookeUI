"""What a volume's file states, read by the app's own Python in a process of its own (registrations.add_volume, when
brainreg isn't installed here but a new kind of job on volumes is: kinds.py, C6).

    python -I volume_inspect.py <file.tif> <output folder>

Only the file's headers are read (tifffile): its series with their axes, shape, type, channel and time counts, and voxel
sizes where its OME or ImageJ metadata states them; its middle plane is decoded for a preview only when stored one plane
a page and at most PREVIEW_MAX_BYTES. The answer is one JSON object on stdout, as brainreg's `inspect` returns it; it is
the parent that bounds its time (registrations.INSPECT_S)."""
from __future__ import annotations

import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PREVIEW_MAX_BYTES = 16 << 20  # one source plane decoded for the preview; its peak about 6x (float32 work, uint8 out)
PEAK_MB = 160  # what it may hold at once, reserved by the parent (registrations._inspect_here, JobMemory)


def series_facts(series, voxel=None) -> dict:
    axes, shape = series.axes, series.shape
    count = {a: shape[axes.index(a)] for a in "CTS" if a in axes}
    spatial = [a for a in axes if a not in "CTS"]
    return {"axes": axes, "spatial": "".join(spatial), "shape": [int(shape[axes.index(a)]) for a in spatial],
            "dtype": str(series.dtype), "channels": int(count.get("C", 1) * count.get("S", 1)),
            "times": int(count.get("T", 1)), "voxel_um": voxel}


def voxel_um(tf, index: int = 0):
    """Voxel sizes in µm (z, y, x) where the file states them (its OME Pixels, or ImageJ's spacing and resolution)."""
    if tf.is_ome:
        images = ET.fromstring(tf.ome_metadata).findall("{*}Image")
        pixels = images[index].find("{*}Pixels") if index < len(images) else None
        if pixels is None:
            return None
        units = {"µm": 1.0, "um": 1.0, "micron": 1.0, "nm": 1e-3, "mm": 1e3}
        sizes = []
        for a in "ZYX":
            value, unit = pixels.get(f"PhysicalSize{a}"), pixels.get(f"PhysicalSize{a}Unit", "µm")
            if value is None or unit not in units:
                return None
            sizes.append(float(value) * units[unit])
        return sizes
    if index != 0:
        return None
    ij = tf.imagej_metadata or {}
    page = tf.pages[0]
    if ij.get("spacing") and "XResolution" in page.tags and str(ij.get("unit", "")).lower() in ("micron", "µm", "um"):
        x, y = page.tags["XResolution"].value, page.tags["YResolution"].value
        return [float(ij["spacing"]), y[1] / y[0], x[1] / x[0]]
    return None


def main() -> int:
    import numpy as np
    import tifffile

    path, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    with tifffile.TiffFile(path) as tf:
        series = [series_facts(s, voxel_um(tf, i)) for i, s in enumerate(tf.series)]
        first, why, outputs = tf.series[0] if tf.series else None, "", {}
        if first is not None and len(series[0]["shape"]) == 3:
            pages = first.pages
            plane_bytes = math.prod(first.keyframe.shape) * first.dtype.itemsize
            if len(pages) != series[0]["shape"][0] * series[0]["channels"] * series[0]["times"]:
                why = "no preview: its series isn't stored a plane at a time"
            elif plane_bytes > PREVIEW_MAX_BYTES:
                why = "no preview: one plane is too large"
            else:
                plane = np.asarray(pages[series[0]["shape"][0] // 2].asarray(), dtype=np.float32)
                plane = plane if plane.ndim == 2 else plane[..., 0]
                lo, hi = np.percentile(plane, (1, 99.5)) if plane.size else (0, 1)
                grey = np.clip((plane - lo) / max(hi - lo, 1e-9) * 255, 0, 255).astype(np.uint8)
                import cv2

                cv2.imwrite(str(out / "preview.png"), grey)
                outputs["preview"] = "preview.png"
    voxel = series[0].get("voxel_um") if series else None
    print(json.dumps({"series": series, "voxel_um": voxel if len(series) == 1 else None, "outputs": outputs,
                      "says": (f"{len(series)} series; the first {' × '.join(map(str, series[0]['shape']))} "
                               f"({series[0]['axes']})" if series else "no image stack")
                              + ((f", voxels {voxel} µm" if voxel else ", voxel sizes not stated") if series else "")
                              + (f"; {why}" if why else "")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
