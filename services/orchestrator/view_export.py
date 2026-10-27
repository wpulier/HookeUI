"""The exact-view export contract (ticket 59's MT7; the QuPath #2211 case): an export renders exactly what it was asked
for at dispatch, and says so, or refuses.

- **Taken at dispatch** (`capture`): the resource (id, revision, digest), the frame token the person was looking at, the
  dimension state (every axis and its step, which axes are displayed), the camera, each layer's style and visibility,
  the projection, the scope, an explicit region when the scope is a region, and the requested size. Whatever happens to
  the live view after that, the export renders the dispatch state, never the newest one.
- **Three scopes, never inferred:** *Current view* (the displayed plane as it is drawn), *Selected region* (an explicit
  region the person chose: an annotation that happens to be selected is never taken as one) and *Scientific data* (the
  native values, which stay a separate export of their own: here only refused with where to go).
- **Refused, never defaulted:** a stale frame (the view changed and the frame token is gone), a missing step for an axis
  that isn't displayed (no Z=0 by default), or a region outside the plane. The words say to choose the view again.
- **Checked:** the written image is reopened and compared with the preview it was promised from (the dispatch frame's own
  pixels, cropped for a region) within a declared rendering tolerance; a difference beyond it is a failed export, kept
  as evidence, never presented as success.
- **Retained:** each export's provenance (the dispatch record, the renderer entry and generation that made it, the image
  and its hash) is written beside the image and indexed in `<catalog>/maintenance/exports.jsonl`, so that when a fix
  for that renderer is applied (maintenance's `[[fixes]]`), the exports an affected version made are listed and offered
  for re-export, never rewritten (`affected`).

Nothing here draws: `render` is the integration's own renderer, called with the dispatch record.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Callable, Mapping

import numpy as np

SCOPES = ("current-view", "selected-region", "scientific-data")
WORDS = {"current-view": "Current view", "selected-region": "Selected region", "scientific-data": "Scientific data"}
TOLERANCE = 2  # the largest per-channel difference (0-255) a rendered export may have from its preview
FORMAT = "hooke-view-export-v1"


class Refused(ValueError):
    """The export can't be made as asked: what is wrong, and `next`, what to do (choose the view again, a region...)."""

    def __init__(self, why: str, *, next: str | None):
        super().__init__(why)
        self.next = next


def capture(frame: dict, *, scope: str, region: dict | None = None, size: tuple[int, int] | None = None,
            current: Callable[[], str] | None = None) -> dict:
    """The dispatch record, or Refused. `frame` is the scientific host's frame record (scientific-host-v1): `frame`,
    `dimensions` {order, displayed, steps}, `camera`, `layers`, and `resource` {id, revision, digest}; `current` says the
    frame token on screen now, to refuse a stale one."""
    if scope not in SCOPES:
        raise Refused(f"there is no export of {scope!r}", next=f"choose what to export: {', '.join(WORDS.values())}")
    if scope == "scientific-data":
        raise Refused("scientific data is exported as native values, not as a picture",
                      next="use Export native data + metadata")
    if size is not None and (type(size) not in (tuple, list) or len(size) != 2
                             or any(type(n) is not int or n <= 0 for n in size)):
        raise Refused("the requested export size needs positive integer width and height", next="choose the size again")
    token = frame.get("frame")
    if not token or (current is not None and current() != token):
        raise Refused("the view changed since it was shown", next="choose the view again, then export")
    dims = frame.get("dimensions") or {}
    order, displayed, steps = list(dims.get("order") or []), list(dims.get("displayed") or []), dims.get("steps")
    if not order or len(displayed) != 2 or not isinstance(steps, list) or len(steps) != len(order):
        raise Refused("the view's position isn't complete", next="choose the view again, then export")
    if any(not isinstance(s, int) or s < 0 for i, s in enumerate(steps) if i not in displayed):
        raise Refused("the view's plane isn't known for every axis (no plane is assumed)",
                      next="choose the view again, then export")
    resource = frame.get("resource") or {}
    if not all(resource.get(k) for k in ("id", "revision", "digest")):
        raise Refused("the data shown has no saved revision to name", next="save it, then export")
    if scope == "selected-region":
        if not isinstance(region, dict) or not all(isinstance(region.get(k), int) for k in ("x", "y", "width", "height")):
            raise Refused("no region was chosen (a selected annotation isn't taken as one)",
                          next="choose a region to export")
        if region["width"] <= 0 or region["height"] <= 0:
            raise Refused("the region has no size", next="choose the region again")
    dispatch = {"format": FORMAT, "scope": scope, "scopeWords": WORDS[scope], "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "resource": {k: resource[k] for k in ("id", "revision", "digest")}, "frame": token,
            "dimensions": {"order": order, "displayed": displayed, "steps": [int(s) for s in steps],
                           "labels": dims.get("labels")},
            "camera": frame.get("camera") or {}, "layers": frame.get("layers") or [],
            "projection": frame.get("projection"), "region": region if scope == "selected-region" else None,
            "size": list(size) if size else None}
    try:
        encoded = json.dumps(dispatch, allow_nan=False)
        if len(encoded.encode('utf-8')) > 1 << 20:
            raise ValueError('the dispatch metadata exceeds its allowance')
        return json.loads(encoded)  # Own every nested value before queued work can observe later view changes.
    except (TypeError, ValueError, OverflowError) as error:
        raise Refused('the view metadata is not a bounded finite export record', next='choose the view again') from error


def _crop(image: np.ndarray, region: dict | None) -> np.ndarray:
    if region is None:
        return image
    y, x, h, w = region["y"], region["x"], region["height"], region["width"]
    if y < 0 or x < 0 or y + h > image.shape[0] or x + w > image.shape[1]:
        raise Refused("the region lies outside the view", next="choose the region again")
    return image[y:y + h, x:x + w]


def check(dispatch: dict, image: np.ndarray, preview: np.ndarray, tolerance: int = TOLERANCE) -> list[str]:
    """What the exported image breaks of its contract, in words ([] when it holds): its size, and how far its pixels are
    from the preview it was promised from (cropped to the region)."""
    expected = _crop(preview, dispatch.get("region"))
    requested = dispatch.get('size')
    if requested is not None and requested != [expected.shape[1], expected.shape[0]]:
        return [f"the requested size is {requested}; this renderer contract supports only the retained view size "
                f"{expected.shape[1]}×{expected.shape[0]}"]
    if image.shape != expected.shape:
        return [f"it is {image.shape[1]}×{image.shape[0]}, the view it was asked from is {expected.shape[1]}×{expected.shape[0]}"]
    worst = int(np.abs(image.astype(np.int16) - expected.astype(np.int16)).max()) if image.size else 0
    return [] if worst <= tolerance else [f"its pixels differ from the view it was asked from by up to {worst} (tolerance "
                                          f"{tolerance}): it isn't the plane that was shown"]


def export(dispatch: dict, render: Callable[[dict], np.ndarray], preview: np.ndarray, out: Path, *, entry: str,
           generation: str, version: str, environ: Mapping[str, str] = os.environ) -> dict:
    """Render the dispatch record, write the PNG, reopen it, check it against the preview, and retain its provenance.
    Returns the provenance ({"ok", "problems", ...}); a failed check is kept as evidence and said, never shown as done."""
    import cv2

    dispatch = copy.deepcopy(dispatch)
    preview = np.array(preview, copy=True)
    if problems := check(dispatch, _crop(preview, dispatch.get('region')), preview):
        raise Refused('; '.join(problems), next='choose the retained view size, then export')
    image = np.ascontiguousarray(render(copy.deepcopy(dispatch)))
    out.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(out), cv2.cvtColor(image, cv2.COLOR_RGB2BGR)):
        raise Refused(f"the image couldn't be written to {out}", next="choose another folder, or free some space")
    reopened = cv2.cvtColor(cv2.imread(str(out), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)  # what the file really holds
    problems = check(dispatch, reopened, preview)
    record = {**dispatch, "ok": not problems, "problems": problems, "file": str(out),
              "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
              "pixelSha256": hashlib.sha256(reopened.tobytes()).hexdigest(),
              "renderer": {"entry": entry, "generation": generation, "version": version}}
    out.with_suffix(".export.json").write_text(json.dumps(record, indent=1) + "\n")
    from .maintenance import store

    store.append(store.path("exports.jsonl", environ), {"at": record["at"], "file": str(out), "entry": entry,
                                                         "generation": generation, "version": version,
                                                         "scope": dispatch["scope"], "ok": record["ok"]})
    return record


def affected(entry: str, versions: list[str], environ: Mapping[str, str] = os.environ) -> list[dict]:
    """The retained exports an affected version of the renderer made (a `[[fixes]]` row's versions): offered for
    re-export with the fixed version, never rewritten. Whether each really shows the defect is unknown (it depends on
    what was in view), and said so."""
    from .maintenance import store

    return [r for r in store.lines(store.path("exports.jsonl", environ))
            if r.get("entry") == entry and str(r.get("version")) in versions and Path(r.get("file", "")).is_file()]


def reexport(record_path: Path, render: Callable[[dict], np.ndarray], preview: np.ndarray, *, entry: str,
             generation: str, version: str, environ: Mapping[str, str] = os.environ) -> dict:
    """A retained export made again from its own dispatch record with another renderer version, beside the original
    (`<name>.reexport-<version>.png`): the original and its provenance stay as they were."""
    original = json.loads(Path(record_path).read_text())
    dispatch = {k: original[k] for k in ("format", "scope", "scopeWords", "at", "resource", "frame", "dimensions",
                                         "camera", "layers", "projection", "region", "size")}
    image = Path(original["file"])
    out = image.with_name(f"{image.stem}.reexport-{version}{image.suffix}")
    return {**export(dispatch, render, preview, out, entry=entry, generation=generation, version=version,
                     environ=environ), "reexportOf": str(image)}
