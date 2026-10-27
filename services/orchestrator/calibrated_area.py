"""Calibrated area and volume from labels' own calibration (ticket 59's MT6), or why not.

A labels resource's calibration (gpu_worker/semantic_resources.py `_calibration`: `axes`, `units`, `affine` rows) maps
array indices to physical positions. When it is axis-aligned (each spatial axis scales only by itself: no rotation or
shear) and every spatial axis has a length unit this module knows, one array element covers the product of the axes'
absolute scales, in µm per axis, also when the axes differ (anisotropic pixels). Anything else keeps pixel counts, and
says why, never a guessed unit or an assumed isotropy.
"""
from __future__ import annotations

import math

UM = {"nm": 1e-3, "nanometer": 1e-3, "nanometre": 1e-3, "µm": 1.0, "μm": 1.0, "um": 1.0, "micrometer": 1.0,
      "micrometre": 1.0, "micron": 1.0, "mm": 1e3, "millimeter": 1e3, "millimetre": 1e3, "m": 1e6, "meter": 1e6,
      "metre": 1e6}


def element_size(calibration: dict | None, axes: list[str] | tuple[str, ...]) -> tuple[float | None, str]:
    """(µm² or µm³ one array element covers, "") or (None, why not)."""
    if not isinstance(calibration, dict) or not calibration.get("axes"):
        return None, "the labels have no calibration, so their size is a count of pixels"
    cal_axes = list(calibration["axes"])
    units, affine = list(calibration.get("units") or []), list(calibration.get("affine") or [])
    if len(units) != len(cal_axes) or len(affine) != len(cal_axes):
        return None, "the labels' calibration is incomplete"
    size = 1.0
    for axis in axes:
        if axis not in cal_axes:
            return None, f"axis {axis} has no calibration"
        i = cal_axes.index(axis)
        unit = str(units[i]).strip()
        if unit not in UM:
            return None, f"axis {axis} is in {unit or 'no unit'}, not a length this app converts"
        row = affine[i]
        if any(abs(float(v)) > 1e-12 for j, v in enumerate(row[:len(cal_axes)]) if j != i):
            return None, "the labels' calibration rotates or shears the axes, so one pixel's area isn't one number"
        scale = abs(float(row[i]))
        if scale <= 0:
            return None, f"axis {axis} has no scale"
        converted = scale * UM[unit]
        if not math.isfinite(converted) or converted <= 0:
            return None, f"axis {axis}'s scale cannot be represented as a positive finite number in µm"
        size *= converted
    if not math.isfinite(size) or size <= 0:
        return None, "the calibrated element size cannot be represented as a positive finite number"
    return size, ""


def unit(dimensions: int) -> str:
    return "µm²" if dimensions == 2 else "µm³"
