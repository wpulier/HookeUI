"""Original still-image inputs, separate from the bridge's fitted display preview.

The first supported scientific import is single-plane, 8-bit grayscale/RGB PNG and JPEG.
Other assets can still be previewed; analysis never substitutes their camera preview.

A still larger than a camera frame is outlined whole on its working copy (`working_copy`): each pixel the mean of a
whole-number block of the original's, so what the model finds there maps back onto the original exactly, and the
result is kept, measured and drawn in the original's own pixels (population.segment).
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from .capture import TEMPORARY_SHARE, Capture, captures
from .brain.registry import ToolFailed

MAX_BYTES = 16 * 1024 * 1024
# The most pixels a whole-view operation (every cell, every particle) is shown at once: a camera frame's, the size its
# models, its timeouts and the memory allowances are made for (capture.py: at 1920x1080 a cell run holds about 47 MB).
# A larger still is shown its working copy (working_copy) instead of being refused (live, 30 Oct: a 12 MP photo needed
# 176 MB of a 160 MB allowance, and Cellpose-SAM's tiles at full size outrun its 180 s call).
WORKING_PIXELS = 1920 * 1080
WORKING_METHOD = ("area average of each s x s block of the original's pixels (OpenCV INTER_AREA), the right and "
                  "bottom edges replicated to whole blocks")


def descriptor(asset, session: str) -> str | None:
    if asset is None or asset.kind != "still":
        return None
    source = ({"retained": asset.exact_reference()} if hasattr(asset, 'exact_reference')
              else {"path": str(asset.path)})
    return json.dumps({"asset": asset.id, **source, "session": session,
                       "optics": asset.scale_optics()})


def _shape(data: bytes) -> tuple[int, int, int, str]:
    """Read dimensions before reserving decoded memory. Never ask a decoder to reduce depth/channels."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 33 and data[12:16] == b"IHDR":
        width, height, depth, color = struct.unpack(">IIBB", data[16:26])
        if depth != 8 or color not in (0, 2):
            raise ValueError("the PNG's depth or channels are unsupported for scientific analysis")
        offset = 8
        while offset + 12 <= len(data):
            length = int.from_bytes(data[offset:offset + 4], "big")
            if offset + length + 12 > len(data):
                raise ValueError("the PNG is incomplete")
            if data[offset + 4:offset + 8] == b"acTL":
                raise ValueError("animated or multi-plane images are unsupported for scientific analysis")
            offset += length + 12
        return width, height, 1 if color == 0 else 3, "image/png"
    if data[:2] == b"\xff\xd8":
        offset = 2
        while offset + 4 < len(data) and data[offset] == 255:
            offset += 1
            while offset < len(data) and data[offset] == 255:
                offset += 1
            if offset >= len(data):
                raise ValueError("the JPEG is incomplete")
            marker = data[offset]
            offset += 1
            if marker in (0xDA, 0xD9):
                break
            length = int.from_bytes(data[offset:offset + 2], "big")
            if length < 2 or offset + length > len(data):
                break
            if marker in (0xC0, 0xC1, 0xC2) and length >= 8:
                depth, height, width, channels = struct.unpack(">BHHB", data[offset + 2:offset + 8])
                if depth != 8 or channels not in (1, 3):
                    raise ValueError("the JPEG's depth or channels are unsupported for scientific analysis")
                return width, height, channels, "image/jpeg"
            offset += length
    raise ValueError("scientific analysis currently requires a single-plane 8-bit grayscale or RGB PNG/JPEG")


def _read(store, spec: dict, projects=None) -> Capture:
    try:
        if spec.get('unavailable'):
            raise ValueError(spec['unavailable'])
        retained = None
        if 'retained' in spec:
            from .project_stage_media import reference
            from .project_media import ProjectMedia
            if projects is None:
                raise ValueError('retained Project media is unavailable on this host')
            selected = reference(spec['retained'], spec['session'])
            metadata, retained = ProjectMedia(projects).read(selected)
            if metadata['asset']['id'] != spec['asset'] or metadata['asset']['kind'] != 'still':
                raise ValueError('the retained original does not match this picture')
            size = retained.size
        else:
            path = Path(spec["path"])
            size = path.stat().st_size
        if size > MAX_BYTES:
            raise ValueError("the original picture exceeds the 16 MiB import limit")
        with store.memory.reserve("analysis", size * 2, "reading original image"):
            if retained is not None:
                from .scientific_files import CHUNK_BYTES
                data = b''.join(retained.read(offset, min(CHUNK_BYTES, size - offset))
                                for offset in range(0, size, CHUNK_BYTES))
                if hashlib.sha256(data).hexdigest() != retained.sha256:
                    raise ValueError('the retained original failed its content hash check')
            else:
                with path.open("rb") as stream:
                    data = stream.read(size + 1)
            if len(data) != size:
                raise ValueError("the original image changed while it was being read")
            width, height, channels, media = _shape(data)
            if min(width, height) <= 0:
                raise ValueError("the original image has invalid dimensions")
            # Opening holds the decoded pixels and their display preview (a JPEG is never larger than the pixels it
            # encodes, past its headers); the pixels' hash reads them in place. Nothing of a cell run is held here:
            # population.segment reserves that, at the size the model is shown.
            with store.memory.reserve("analysis", int((2 * width * height * channels + 64 * 1024) * (1 + TEMPORARY_SHARE)),
                                      "opening original image"):
                pixels = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
                shape = (height, width) if channels == 1 else (height, width, 3)
                if pixels is None or pixels.shape != shape or pixels.dtype != np.uint8:
                    raise ValueError("the original image cannot be decoded at its declared shape and depth")
                source = {"asset": spec["asset"], "session": spec["session"], "sha256": hashlib.sha256(data).hexdigest(),
                          "pixelSha256": hashlib.sha256(np.ascontiguousarray(pixels)).hexdigest(), "width": width, "height": height,
                          "dtype": "uint8", "axes": "YX" if channels == 1 else "YXC", "channels": channels,
                          "pixelEncoding": "row-major uint8 grayscale" if channels == 1 else "row-major uint8 BGR",
                          "mediaType": media, "units": "pixels", "orientation": "encoded pixel grid; EXIF rotation not applied"}
                if retained is not None:
                    source['retained'] = dict(spec['retained'])
                ok, preview = cv2.imencode(".jpg", pixels)
                if not ok:
                    raise ValueError("the original image's display preview could not be made")
                scale = {"units": "pixels", "frame": [width, height], "asset": spec["asset"]}
                per_pixel = (metadata["asset"].get("optics", {}) if retained is not None
                             else spec.get("optics", {})).get("umPerPx")
                if isinstance(per_pixel, (int, float)) and np.isfinite(per_pixel) and per_pixel > 0:
                    scale.update(umPerPx=per_pixel, measured=True, source="asset", units="µm")
                    source.update(umPerPx=per_pixel, units="µm", calibrationSource="asset")
                info = SimpleNamespace(width=width, height=height, epoch=None, dx=0.0, dy=0.0, fov=None, disc=0.0,
                                       tracked=False, steady=True, wall_at=time.time(), seq=None, boot="")
                return store.original(info, preview.tobytes(), scale, data, source)
    except (OSError, ValueError, cv2.error) as exc:
        raise ToolFailed(f"Cannot analyze the original picture: {exc}",
                         next="export one 8-bit grayscale or RGB PNG plane, keeping the original, then add it here") from None


def working_scale(width: int, height: int) -> int:
    """The smallest whole number s whose s x s blocks bring a `width` x `height` still within WORKING_PIXELS (1: it is
    analysed as it is). Whole blocks keep the mapping back exact: a working pixel is exactly s x s original pixels."""
    scale = 1
    while -(-int(width) // scale) * -(-int(height) // scale) > WORKING_PIXELS:
        scale += 1
    return scale


def _points(polygon, s: int) -> list:
    return [[float(x) * s, float(y) * s] for x, y in polygon]


@dataclass(frozen=True)
class Working:
    """A large still's working copy (`working_copy`): the picture a whole-view operation is shown in place of the
    original. Its pixel (i, j) is the mean of the original's block [i s, (i + 1) s) x [j s, (j + 1) s), so a working
    pixel index x is the original's x s, as a mask kept at the model's scale is placed (measure.Mask)."""
    capture: Capture  # a still of its own (its own id), never the original's id
    scale: int  # original px per working px, on each axis
    size: tuple[int, int]  # the working copy's width, height
    of: tuple[int, int]  # the original's width, height
    pixel_sha256: str  # of the working pixels, row-major uint8 (as sourceImage's pixelSha256 is of the original's)
    sha256: str  # of the PNG the model is sent

    def wire(self) -> dict:
        """What was done to the original before the model saw it, kept with the result (producer `workingInput`)."""
        return {"scale": self.scale, "size": list(self.size), "of": list(self.of), "method": WORKING_METHOD,
                "pixelSha256": self.pixel_sha256, "sha256": self.sha256, "mediaType": "image/png"}

    def crop(self, region) -> tuple[int, int, int, int] | None:
        """An original-pixel region (x, y, w, h) as the working copy's blocks that cover it."""
        if region is None:
            return None
        x, y, w, h = (int(v) for v in region)
        s = self.scale
        x0, y0, x1, y1 = x // s, y // s, -(-(x + w) // s), -(-(y + h) // s)
        return (x0, y0, x1 - x0, y1 - y0)

    def to_original(self, output: dict) -> dict:
        """An operation's output on the working copy, in the original's pixels: a label map keeps its own pixels and
        says the scale it sits at (population.members_from places each mask by it), automatic masks the same, and
        polygons are moved onto the original's grid. A new dict: a job's output is shared by every request that
        waited on it, so it is never changed in place."""
        s = self.scale
        out = dict(output)
        if isinstance(output.get("labels"), dict):
            labels = dict(output["labels"])
            ox, oy = labels.get("origin") or (0, 0)
            labels.update(origin=[int(ox) * s, int(oy) * s], scale=float(labels.get("scale") or 1) * s)
            out["labels"] = labels
        if output.get("regions"):
            regions = []
            for region in output["regions"]:
                region = dict(region)
                if isinstance(region.get("mask"), dict):
                    mask = dict(region["mask"])
                    mask.update(x0=float(mask.get("x0", 0)) * s, y0=float(mask.get("y0", 0)) * s,
                                scale=float(mask.get("scale") or 1) * s)
                    region["mask"] = mask
                if region.get("polygons"):
                    region["polygons"] = [_points(p, s) for p in region["polygons"]]
                if region.get("label"):
                    region["label"] = [float(v) * s for v in region["label"]]
                for key, power in (("inner", 1), ("area", 2)):
                    if isinstance(region.get(key), (int, float)):
                        region[key] = region[key] * s ** power
                regions.append(region)
            out["regions"] = regions
        if output.get("polygons"):
            out["polygons"] = [_points(p, s) for p in output["polygons"]]
        return out


def working_copy(store, capture) -> Working | None:
    """The working copy of an original still larger than WORKING_PIXELS, or None for one that is not (or a camera
    frame): made in a thread, from the original's own bytes (its encoded grid, as `_read` checked it), each whole
    s x s block averaged (an edge short of a block replicated to fill it), and encoded losslessly. Its making is
    reserved in the analysis allowance and let go once made; it is held as a still of its own (`Captures.original`)
    with the original's source and scale said for its grid. Runs using it at once share it (`Captures.working`); it
    is let go with the last of them, and made again for a later run."""
    native = getattr(capture, "native", None)
    if native is None:
        return None
    width, height = int(capture.width), int(capture.height)
    s = working_scale(width, height)
    if s == 1:
        return None
    known = store.working.get(capture.id)
    if known is not None:
        return known
    source = copy.deepcopy(capture.source_image or {})
    channels = 1 if source.get("channels") == 1 else 3
    w, h = -(-width // s), -(-height // s)
    full, small = width * height * channels, w * h * channels
    padded = w * s * h * s * channels if (w * s, h * s) != (width, height) else 0  # an edge short of a whole block
    try:
        with store.memory.reserve("analysis", int((full + padded + 3 * small + 64 * 1024) * (1 + TEMPORARY_SHARE)),
                                  "making the large still's working copy"):
            pixels = cv2.imdecode(np.frombuffer(native, np.uint8), cv2.IMREAD_UNCHANGED)
            shape = (height, width) if channels == 1 else (height, width, 3)
            if pixels is None or pixels.shape != shape or pixels.dtype != np.uint8:
                raise ValueError("the original image cannot be decoded at its declared shape and depth")
            if (w * s, h * s) != (width, height):
                pixels = cv2.copyMakeBorder(pixels, 0, h * s - height, 0, w * s - width, cv2.BORDER_REPLICATE)
            reduced = np.ascontiguousarray(cv2.resize(pixels, (w, h), interpolation=cv2.INTER_AREA))
            pixels = None
            ok, png = cv2.imencode(".png", reduced)
            shown, preview = cv2.imencode(".jpg", reduced)
            if not (ok and shown):
                raise ValueError("its working copy could not be encoded")
            pixel_sha256 = hashlib.sha256(reduced).hexdigest()
            reduced = None
    except (ValueError, cv2.error) as exc:
        raise ToolFailed(f"Cannot analyze the original picture: {exc}",
                         next="export one 8-bit grayscale or RGB PNG plane, keeping the original, then add it here") from None
    png, sha256 = png.tobytes(), hashlib.sha256(png).hexdigest()
    info = SimpleNamespace(width=w, height=h, epoch=None, dx=0.0, dy=0.0, fov=None, disc=0.0, tracked=False,
                           steady=True, wall_at=capture.at, seq=None, boot="")
    scale = copy.deepcopy(capture.scale) if isinstance(capture.scale, dict) else {"units": "pixels"}
    scale["frame"] = [w, h]
    per_pixel = scale.get("umPerPx")
    if isinstance(per_pixel, (int, float)) and np.isfinite(per_pixel) and per_pixel > 0:
        scale["umPerPx"] = per_pixel * s
    working_source = {**source, "width": w, "height": h, "sha256": sha256, "pixelSha256": pixel_sha256,
                      "mediaType": "image/png",
                      "workingCopyOf": {"width": width, "height": height, "scale": s, "sha256": source.get("sha256"),
                                        "pixelSha256": source.get("pixelSha256"), "mediaType": source.get("mediaType")}}
    if isinstance(source.get("umPerPx"), (int, float)):
        working_source["umPerPx"] = source["umPerPx"] * s
    made = Working(store.original(info, preview.tobytes(), scale, png, working_source), s, (w, h), (width, height),
                   pixel_sha256, sha256)
    return store.working.setdefault(capture.id, made)  # one made meanwhile by another run is the one shared


async def for_analysis(hub, capture=None):
    if capture is not None and getattr(capture, "native", None) is not None:
        return capture
    spec = (getattr(capture, "source_image_json", None) if capture is not None
            else getattr(hub, "stage_image_json", None))
    if spec is None:
        return capture
    # Freeze before awaiting disk work: later source/session changes cannot relabel these bytes.
    return await asyncio.to_thread(_read, captures(hub), json.loads(spec),
                                   getattr(getattr(hub, 'resources', None), 'projects', None))


async def worker_input(capture) -> bytes:
    """Keep the encoded grid even in model decoders that apply JPEG EXIF rotation by default."""
    if (getattr(capture, "source_image", None) or {}).get("mediaType") != "image/jpeg":
        return getattr(capture, "analysis_bytes", capture.jpeg)

    def encode():
        pixels = cv2.imdecode(np.frombuffer(capture.analysis_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
        ok, data = cv2.imencode(".png", pixels)
        if not ok:
            raise ToolFailed("Cannot prepare the original JPEG pixels", next="export this picture as PNG and add it again")
        return data.tobytes()

    return await asyncio.to_thread(encode)
