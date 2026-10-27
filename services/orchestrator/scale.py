"""How big things are: microns per pixel from the objective in use (roadmap W4-1).

The phone looks down the eyepiece, so the bright disc it sees is the eyepiece's field of view:
its width in micrometres is the eyepiece's field number (mm) divided by the objective's
magnification, and the disc's width in pixels is measured on every frame (orchestrator/fov.py).
So once the user says which objective is in use ("I'm on 40x"), every outline has a size:

  field width (µm) = field number x 1000 / objective     an estimate (the field number is assumed
                                                          unless the user gives it; 18 mm is the
                                                          common teaching eyepiece)
  field width (µm) = known length / its length in px x disc width in px   measured, per objective,
                                                          when the user says how long something is
                                                          ("this line of the stage micrometer is
                                                          100 µm")

Kept until changed (the user's preference, day/prefs.py), so a restart or a new day keeps it. Without an objective there is no
scale, and no size is ever stated (W1-5): the models never give sizes themselves either; the app
measures them here.

Each value says where it came from, for datasets (docs/datasets.md): the field number is the
`default` until someone gives it (`user`, with who and when; a day saved before this was kept
says `unknown`, because 18 then looked the same whether it was given or assumed), the objective
says who set it and when, and a calibration keeps the length it was made from.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np

DEFAULT_FIELD_NUMBER = 18.0  # mm: WF10x/18, the usual eyepiece of a teaching microscope
# Field numbers eyepieces have: a WF25x is about 9-10 mm, a wide WF10x up to about 26 (2026-10-06: a
# 25x eyepiece was in use, which the earlier 10-30 mm range could not take).
FIELD_NUMBER_RANGE = (5.0, 30.0)
OBJECTIVES = (4, 10, 20, 40, 60, 100)
PREF = "scale"  # the day preference it is kept in (day/store.py)


@dataclass(frozen=True)
class Reading:
    um_per_px: float
    field_um: float
    objective: int
    measured: bool  # True: calibrated with a known length; False: from the objective and field number

    @property
    def words(self) -> str:
        how = "measured" if self.measured else "estimated from the objective"
        at = f" at {self.objective}x" if self.objective else ""
        return f"the view is about {round_um(self.field_um)} µm across{at} ({how})"


def round_um(value: float) -> str:
    """A size as a person would say it: 2 significant figures, no false precision."""
    if value >= 100:
        return f"{round(value, -1 if value < 1000 else -2):.0f}"
    if value >= 10:
        return f"{round(value):.0f}"
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _calibration_facts(calibration: dict) -> dict:
    """Project saved facts without repairing their original preference or provenance."""
    facts = dict(calibration)
    invalid = []
    for field in ("fieldUm", "lengthUm", "lengthPx", "discRadiusPx", "at"):
        if field not in facts:
            continue  # Older measured widths have no input/time provenance.
        value = facts[field]
        if value is None and field == "at":
            continue
        if value is None or not math.isfinite(value):
            facts[field] = None
            invalid.append(field)
        elif field == "fieldUm" and value <= 0:
            invalid.append(field)  # Keep finite facts, even when they cannot be used.
    if invalid:
        facts["unavailable"] = {"reason": "invalid-calibration", "fields": invalid}
    return facts


class Scale:
    def __init__(self, day=None) -> None:
        self.day = day
        state = (day.prefs.get(PREF) if day is not None else None) or {}
        self.objective: int | None = state.get("objective")
        self.field_number: float = float(state.get("fieldNumber") or DEFAULT_FIELD_NUMBER)
        self.calibrated: dict[str, float] = {str(k): float(v) for k, v in (state.get("calibrated") or {}).items()}
        # Where each value came from. A day saved before these were kept cannot tell a given 18 mm
        # from the assumed one: unknown, never "default".
        self.field_number_source: str = state.get("fieldNumberSource") or ("unknown" if "fieldNumber" in state else "default")
        self.field_number_via: str | None = state.get("fieldNumberVia")
        self.field_number_at: float | None = state.get("fieldNumberAt")
        self.objective_via: str | None = state.get("objectiveVia")
        self.objective_at: float | None = state.get("objectiveAt")
        self.calibrations: dict[str, dict] = dict(state.get("calibrations") or {})  # how each was measured
        self.listeners: list = []
        # The asset on stage and its own magnification (library.py; ADR 0022): {"id", "objective"?, "fieldNumber"?,
        # "umPerPx"?}, or None on the live camera. While set, it is the scale (none when it has no magnification), and
        # setting the objective or calibrating describes it: `asset_set(id, fields)` keeps that in its metadata.
        self.asset: dict | None = None
        self.asset_set = None

    # --- setting it -----------------------------------------------------------------------------
    def use_asset(self, aid: str | None, optics: dict | None = None) -> None:
        """An asset put on stage with its own magnification (None: the live camera again, and the objective menu's
        scale). A recording made at 40x is never measured at the objective on the microscope now; an asset with none
        states no sizes until the user sets its optics or calibrates on it."""
        self.asset = None if aid is None else {"id": aid, **{k: float(v) for k, v in (optics or {}).items()
                                                             if k in ("objective", "fieldNumber", "umPerPx") and v is not None}}
        self._tell()

    def set_objective(self, objective: int | None, field_number: float | None = None, via: str | None = None) -> None:
        """`via`: who said so (studio, voice, chat), kept with it. With an asset on stage, it is that asset's objective
        (its metadata), not the microscope's."""
        if objective is not None and not (1 <= int(objective) <= 200):
            raise ValueError(f"{objective}x is not an objective")
        if field_number is not None:
            self._check_field_number(field_number)
        if self.asset is not None:
            fields = {"objective": float(objective) if objective is not None else None,
                      **({"fieldNumber": float(field_number)} if field_number is not None else {})}
            self._asset_fields(fields)
            return
        self.objective = int(objective) if objective is not None else None
        self.objective_via, self.objective_at = via, round(time.time(), 3)
        if field_number is not None:
            self._given_field_number(float(field_number), via)
        self._save()

    def set_field_number(self, field_number: float, via: str | None = None) -> None:
        """The eyepiece's field number, given (the studio's microscope facts, or said)."""
        self._check_field_number(field_number)
        self._given_field_number(float(field_number), via)
        self._save()

    def _asset_fields(self, fields: dict) -> None:
        """The asset on stage's magnification changed (the objective said, or a calibration): kept on it."""
        assert self.asset is not None
        for k, v in fields.items():
            if v is None:
                self.asset.pop(k, None)
            else:
                self.asset[k] = float(v)
        if self.asset_set is not None:
            try:
                self.asset_set(self.asset["id"], fields)
            except Exception:
                pass  # the scale holds it while the asset is on stage whatever happens to its record
        self._tell()

    @staticmethod
    def _check_field_number(field_number: float) -> None:
        low, high = FIELD_NUMBER_RANGE
        if not (low <= float(field_number) <= high):
            raise ValueError(f"a field number of {field_number} mm is outside {low:g}-{high:g} mm")

    def _given_field_number(self, field_number: float, via: str | None) -> None:
        self.field_number = field_number
        self.field_number_source, self.field_number_via, self.field_number_at = "user", via, round(time.time(), 3)

    def facts(self, *, current_only: bool = False) -> dict:
        """Rig provenance; frame intake needs only the active objective's calibration. With an asset on stage, its
        own magnification (source "asset")."""
        if self.asset is not None:
            a = self.asset
            given = a.get("fieldNumber")
            return {"objective": {"value": int(a["objective"]) if a.get("objective") else None, "source": "asset",
                                  "asset": a["id"]},
                    "fieldNumber": {"value": given if given else self.field_number,
                                    "source": "asset" if given else self.field_number_source},
                    "calibrated": {},
                    **({"umPerPx": {"value": a["umPerPx"], "source": "asset", "asset": a["id"]}} if a.get("umPerPx") else {})}
        return {"objective": {"value": self.objective, "source": "user" if self.objective is not None else "unknown",
                              "via": self.objective_via, "at": self.objective_at},
                "fieldNumber": {"value": self.field_number, "source": self.field_number_source,
                                "via": self.field_number_via, "at": self.field_number_at,
                                **({"note": f"assumed: {DEFAULT_FIELD_NUMBER:g} mm, a WF10x/18 eyepiece"}
                                   if self.field_number_source == "default" else {})},
                "calibrated": {k: _calibration_facts({**self.calibrations.get(k, {}), "fieldUm": v})
                               for k, v in self.calibrated.items()
                               if not current_only or k == str(self.objective)}}

    def calibrate(self, length_px: float, length_um: float, disc_radius_px: float) -> Reading:
        """A known length (a stage micrometer line, or something of known size) fixes the field
        width for the current objective. With an asset on stage, it measures that asset (its umPerPx), whatever its
        objective."""
        if self.asset is not None:
            if (not all(math.isfinite(value) for value in (length_px, length_um, disc_radius_px))
                    or length_px <= 2 or length_um <= 0 or disc_radius_px <= 0):
                raise ValueError("that length cannot be measured here")
            um_per_px = length_um / length_px
            self._asset_fields({"umPerPx": um_per_px})
            return Reading(um_per_px, um_per_px * 2 * disc_radius_px, int(self.asset.get("objective") or 0), True)
        if self.objective is None:
            raise ValueError("say which objective is in use first (\"I'm on 40x\")")
        if (not all(math.isfinite(value) for value in (length_px, length_um, disc_radius_px))
                or length_px <= 2 or length_um <= 0 or disc_radius_px <= 0):
            raise ValueError("that length cannot be measured here")
        field_um = length_um / length_px * 2 * disc_radius_px
        um_per_px = field_um / (2 * disc_radius_px)
        if not (math.isfinite(field_um) and field_um > 0 and math.isfinite(um_per_px) and um_per_px > 0):
            raise ValueError("that length does not give a finite positive calibration")
        reading = Reading(um_per_px, field_um, self.objective, True)
        self.calibrated[str(self.objective)] = field_um
        self.calibrations[str(self.objective)] = {"lengthUm": length_um, "lengthPx": round(length_px, 2),
                                                  "discRadiusPx": round(disc_radius_px, 2), "at": round(time.time(), 3)}
        self._save()
        return reading

    def _save(self) -> None:
        if self.day is not None:
            self.day.set_pref(PREF, {"objective": self.objective, "fieldNumber": self.field_number,
                                     "calibrated": dict(self.calibrated),
                                     "fieldNumberSource": self.field_number_source, "fieldNumberVia": self.field_number_via,
                                     "fieldNumberAt": self.field_number_at, "objectiveVia": self.objective_via,
                                     "objectiveAt": self.objective_at, "calibrations": dict(self.calibrations)})
        self._tell()

    def _tell(self) -> None:
        for listener in list(self.listeners):
            try:
                listener()
            except Exception:
                pass

    # --- using it -----------------------------------------------------------------------------
    @staticmethod
    def reading_from(facts: dict, disc_radius_px: float | None) -> Reading | None:
        """A frame's frozen optics and its own disc; never consult the current knobs."""
        objective = (facts.get("objective") or {}).get("value")
        um = (facts.get("umPerPx") or {}).get("value")  # an asset's own, measured on it (library.py)
        if um is not None:
            if (not math.isfinite(um) or um <= 0 or disc_radius_px is None or not math.isfinite(disc_radius_px)
                    or disc_radius_px <= 0):
                return None
            return Reading(um, um * 2 * disc_radius_px, int(objective or 0), True)
        if (objective is None or not math.isfinite(objective) or objective <= 0
                or disc_radius_px is None or not math.isfinite(disc_radius_px) or disc_radius_px <= 0):
            return None
        calibrations = facts.get("calibrated") or {}
        measured = str(objective) in calibrations
        if measured:
            calibration = _calibration_facts(calibrations[str(objective)])
            if calibration.get("unavailable"):
                return None  # An invalid measured fact is not an estimated measurement.
            field_um = calibration.get("fieldUm")
        else:
            field_number = (facts.get("fieldNumber") or {}).get("value")
            if field_number is None or not math.isfinite(field_number) or field_number <= 0:
                return None
            field_um = field_number * 1000.0 / objective
        if field_um is None or not math.isfinite(field_um) or field_um <= 0:
            return None
        um_per_px = field_um / (2 * disc_radius_px)
        if not math.isfinite(um_per_px) or um_per_px <= 0:
            return None
        return Reading(um_per_px, field_um, objective, measured)

    def reading(self, disc_radius_px: float | None) -> Reading | None:
        """The scale now, for a disc of this radius; None without an objective or a disc."""
        return self.reading_from(self.facts(current_only=True), disc_radius_px)

    def wire(self, disc_radius_px: float | None = None) -> dict:
        r = self.reading(disc_radius_px)
        if self.asset is not None:
            # The asset on stage's own (the studio's objective menu describes it while it is there).
            return {"type": "scale", "objective": int(self.asset["objective"]) if self.asset.get("objective") else None,
                    "fieldNumber": self.asset.get("fieldNumber") or self.field_number,
                    "fieldNumberSource": "asset" if self.asset.get("fieldNumber") else self.field_number_source,
                    "measured": [], "asset": self.asset["id"],
                    "umPerPx": round(r.um_per_px, 4) if r else None, "fieldUm": round(r.field_um, 1) if r else None,
                    "source": None if r is None else ("measured" if r.measured else "estimate")}
        return {"type": "scale", "objective": self.objective, "fieldNumber": self.field_number,
                "fieldNumberSource": self.field_number_source,
                "measured": sorted(int(k) for k, calibration in self.facts()["calibrated"].items()
                                   if not calibration.get("unavailable")),
                "umPerPx": round(r.um_per_px, 4) if r else None, "fieldUm": round(r.field_um, 1) if r else None,
                "source": None if r is None else ("measured" if r.measured else "estimate")}


def sizes(polygons, reading: Reading) -> dict:
    """An outline's size: longest and shortest width (Feret) in µm and area in µm²."""
    points = np.array([p for poly in polygons for p in poly], dtype=float)
    if len(points) < 3:
        return {}
    centred = points - points.mean(axis=0)
    # Calipers every 5 degrees: the longest and the shortest width (Feret's diameters).
    extents = [float(np.ptp(centred @ np.array([math.cos(a), math.sin(a)]))) for a in np.linspace(0, math.pi, 36, endpoint=False)]
    longest, shortest = max(extents), min(extents)
    area = filled_area(polygons)
    k = reading.um_per_px
    return {"lengthUm": longest * k, "widthUm": shortest * k, "areaUm2": area * k * k,
            "measured": reading.measured}


def area_px(polygons) -> float:
    """An outline's area in frame pixels (no scale needed): for the logs."""
    return filled_area(polygons)


def filled_area(polygons) -> float:
    """The area an outline covers, filled even-odd as it is drawn: a polygon inside an odd number of the others is a
    hole (a ring's inner edge: gpu_worker.mask_to_polygons) and is taken off."""
    import cv2

    shapes = [np.array(poly, dtype=float) for poly in polygons or [] if len(poly) >= 3]
    total = 0.0
    for i, shape in enumerate(shapes):
        inside = sum(1 for j, other in enumerate(shapes) if j != i and
                     cv2.pointPolygonTest(other.astype(np.float32).reshape(-1, 1, 2), (float(shape[0][0]), float(shape[0][1])), False) > 0)
        total += (-1 if inside % 2 else 1) * abs(_shoelace(shape))
    return max(0.0, total)


def _shoelace(poly: np.ndarray) -> float:
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))


def size_words(size: dict) -> str:
    """'about 12 × 8 µm' (or 'about 12 µm' when round), marked as an estimate when it is one."""
    if not size:
        return ""
    length, width = size["lengthUm"], size["widthUm"]
    text = (f"about {round_um(length)} µm across" if width > 0.8 * length
            else f"about {round_um(length)} × {round_um(width)} µm")
    return text + ("" if size.get("measured") else " (estimated from the objective)")
