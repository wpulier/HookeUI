"""Original still-image inputs, separate from the bridge's fitted display preview.

The first supported scientific import is single-plane, 8-bit grayscale/RGB PNG and JPEG.
Other assets can still be previewed; analysis never substitutes their camera preview.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import struct
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from .capture import Capture, captures, footprint
from .brain.registry import ToolFailed

MAX_BYTES = 16 * 1024 * 1024


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
            with store.memory.reserve("analysis", footprint(width, height, size, "cells"), "opening original image"):
                pixels = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
                shape = (height, width) if channels == 1 else (height, width, 3)
                if pixels is None or pixels.shape != shape or pixels.dtype != np.uint8:
                    raise ValueError("the original image cannot be decoded at its declared shape and depth")
                source = {"asset": spec["asset"], "session": spec["session"], "sha256": hashlib.sha256(data).hexdigest(),
                          "pixelSha256": hashlib.sha256(pixels.tobytes()).hexdigest(), "width": width, "height": height,
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
