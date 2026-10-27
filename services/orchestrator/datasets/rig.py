"""The microscope as the user describes it (the Datasets tab), and the facts a dataset starts with.

`rig.json` (beside the datasets) keeps what only the user can know: the microscope, the eyepiece's
magnification and field number, the objectives on the nosepiece, the phone and how it is held. It
outlives days, as the microscope does. Every fact says where it came from, and nothing is guessed:

  user           the user entered it (with when, and through what)
  measured       the app measured it (the eyepiece disc, the frame size, the frame rate)
  bridge-config  what the camera bridge was asked for, not what it got
  default        assumed by the app (the 18 mm field number), and said to be
  unknown        nobody said

A dataset copies them when it starts (`at_start`); a fact entered while it collects is appended to
it as a `rig-fact` event, marked entered after the start, never written into its past.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

NAME = "rig.json"
# What the user can say about their microscope (the names follow assets.OPTICS_FIELDS where they meet).
FIELDS = {
    "microscope": "the microscope (make and model)",
    "eyepiece": "eyepiece magnification (x)",
    "fieldNumber": "eyepiece field number (mm), printed on the eyepiece after the slash: WF10x/18",
    "objectives": "objectives on the nosepiece",
    "camera": "the phone or camera on the eyepiece",
    "adapter": "how the phone is held to the eyepiece",
    "notes": "anything else about the setup",
}
NUMBERS = {"eyepiece": (1.0, 100.0), "fieldNumber": (5.0, 30.0)}
TEXT_CHARS = 160


class Rig:
    def __init__(self, folder: Path, clock=time.time) -> None:
        self.path = Path(folder) / NAME
        self.clock = clock
        self.facts: dict[str, dict] = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in (data.get("facts") or {}).items() if k in FIELDS and isinstance(v, dict)}

    def set(self, field: str, value, via: str = "studio") -> dict:
        """The user says (or clears, with an empty value) one fact. Returns it as kept."""
        if field not in FIELDS:
            raise ValueError(f"{field!r} is not a microscope fact (one of {', '.join(FIELDS)})")
        clean = clean_value(field, value)
        fact = {"value": clean, "source": "user" if clean is not None else "unknown", "via": via,
                "at": round(self.clock(), 3)}
        if clean is None:
            self.facts.pop(field, None)
        else:
            self.facts[field] = fact
        self._save()
        return fact

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f".{NAME}.tmp")
        temp.write_text(json.dumps({"schema": 1, "facts": self.facts, "fields": FIELDS}, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        os.replace(temp, self.path)

    def wire(self) -> dict:
        return {"type": "rig", "facts": self.facts, "fields": FIELDS}


def clean_value(field: str, value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if field in NUMBERS:
        try:
            number = float(str(value).lower().replace("mm", "").rstrip("x").strip())
        except ValueError:
            raise ValueError(f"{FIELDS[field]} is a number") from None
        low, high = NUMBERS[field]
        if not low <= number <= high:
            raise ValueError(f"{FIELDS[field]}: {number:g} is outside {low:g}-{high:g}")
        return number
    return " ".join(str(value).split())[:TEXT_CHARS]


def at_start(rig: Rig | None, scale, health: dict | None, info, fps: float | None) -> dict:
    """The facts a dataset starts with, each with its source. `scale`: the app's Scale (objective and
    field number with who set them); `health`: the bridge's; `info`: the latest frame's FrameInfo."""
    facts: dict[str, dict] = {}
    for field in FIELDS:
        entered = rig.facts.get(field) if rig is not None else None
        facts[field] = ({**entered, "carriedOver": True} if entered
                        else {"value": None, "source": "unknown"})
    if scale is not None:
        mine = scale.facts()
        facts["objective"] = mine["objective"]
        if facts["fieldNumber"].get("value") is None or mine["fieldNumber"]["source"] == "user":
            facts["fieldNumber"] = mine["fieldNumber"]  # what the scale uses, and where it came from
        facts["calibrated"] = {"value": mine["calibrated"] or None, "source": "measured" if mine["calibrated"] else "unknown"}
        radius = info.fov.radius if info is not None and info.fov is not None else None
        reading = scale.reading(radius) if radius else None
        facts["umPerPx"] = ({"value": round(reading.um_per_px, 5), "source": "measured" if reading.measured else "estimate",
                             "how": "a known length at this objective" if reading.measured else
                             "the field number over the objective, across the eyepiece disc"}
                            if reading else {"value": None, "source": "unknown"})
    if info is not None:
        facts["frameSize"] = {"value": f"{info.width}x{info.height}", "source": "measured"}
        if info.fov is not None:
            facts["eyepieceDisc"] = {"value": {"cx": round(info.fov.cx, 1), "cy": round(info.fov.cy, 1),
                                               "radius": round(info.disc or info.fov.radius, 2)}, "source": "measured"}
    if fps:
        facts["frameRate"] = {"value": round(fps, 2), "source": "measured",
                              "how": "frames reaching the app per second over the last 3 s"}
    if health:
        settings = health.get("settings") or {}
        facts["cameraDevice"] = {"value": health.get("targetDevice"), "source": "bridge-config"}
        facts["cameraRequested"] = {"value": {"size": settings.get("size"), "fps": settings.get("fps")},
                                    "source": "bridge-config"}
    return facts
