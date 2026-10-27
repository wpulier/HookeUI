"""The truth about a corpus tour (bench/corpus/tour.py writes it beside the MP4): what is in the
eyepiece at any moment. Benches ask it about the frame the bridge says is on screen (/sim/status
gives the recording's frame), never the app: the orchestrator cannot tell footage from the phone."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Truth:
    kind: str  # "still" (a tour of a picture) | "video" (a video in the eyepiece)
    specimen: str
    fps: float
    frames: list[dict]
    source: dict

    @classmethod
    def load(cls, path: Path) -> "Truth":
        path = Path(path)
        if path.suffix != ".json":
            path = path.with_suffix(".truth.json")
        data = json.loads(path.read_text())
        return cls(data["kind"], data.get("specimen", ""), float(data["fps"]), data["frames"], data.get("source", {}))

    def frame(self, n: int) -> dict:
        """Frame n of the recording (clamped: the bridge loops recordings)."""
        return self.frames[n % len(self.frames)]

    def objects(self, n: int, label: str | None = None) -> list[dict]:
        """Labelled things in the eyepiece in frame n, optionally only those whose label
        contains `label`."""
        things = self.frame(n)["objects"]
        return [t for t in things if label is None or label.lower() in t["label"].lower()]

    def still(self, n: int, below_px: float = 2.0) -> bool:
        """The view is not moving in frame n (a pause in the tour)."""
        return self.frame(n).get("speed", 0.0) < below_px

    def labels(self) -> list[str]:
        return sorted({t["label"] for f in self.frames for t in f["objects"]})
