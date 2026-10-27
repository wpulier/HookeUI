"""A picture of the view to keep: a real camera frame with the outlines, their labels and the scale
bar drawn on it by the app, with the same geometry as the studio (apps/studio/src/view.ts).

What is drawn, as the studio draws it:
- An outline is stored in its anchor frame's coordinates and drawn at p + (offset_now -
  offset_anchor). Only outlines of the frame's motion epoch are drawn (an older one is being
  re-found and has no verified place), not lost or out-of-view ones, and only when its centre is
  inside the eyepiece (contract inViewRadius). The ones left out are counted, with why.
- The app's outlines in the user's outline colour (a name it is not sure of says "not sure" in
  its label, never by a pale line: as the studio, which could not be seen on a light slide),
  cells as thin pale lines without labels.
- A scale bar only when the scale is known (orchestrator/scale.py), marked "~" when it is an
  estimate from the objective.
- A line at the bottom says it is a real frame with the app's drawing on it.

`draw` is pure (an image in, an image and what was drawn out); the `snapshot` tool saves and shows it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .contract import IN_VIEW_RADIUS
from .drawing import outline

# The studio's colours (view.ts OUTLINE_COLORS, IODINE, PAPER), as BGR.
def _bgr(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)


OUTLINE_BGR = {name: _bgr(c) for name, c in
               {"pink": "#ff4fa3", "yellow": "#fff04a", "cyan": "#3fe3ff", "green": "#59ff8f", "white": "#ffffff"}.items()}
IODINE = _bgr("#ffb547")  # "looking"
PAPER = _bgr("#e7ecf7")  # cells
INK = _bgr("#131a2b")  # label backgrounds
NICE_UM = (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000)  # overlay.ts scaleBar
FONT = cv2.FONT_HERSHEY_SIMPLEX


@dataclass
class Drawn:
    image: np.ndarray
    crop: tuple[int, int, int, int]  # the part of the frame kept (x0, y0, x1, y1)
    outlines: list[dict] = field(default_factory=list)  # what was drawn: id, label, status, size
    left_out: dict[str, int] = field(default_factory=dict)  # why outlines were not drawn: count
    scale: dict | None = None  # the bar drawn: um, umPerPx, objective, measured


def placed(obj, info) -> tuple[list[np.ndarray] | None, str]:
    """The outline's polygons in the frame's coordinates, or None and why it is not drawn."""
    if obj.track in ("lost", "out_of_view"):
        return None, "out of view"
    if obj.anchor_epoch != info.epoch:
        return None, "being re-found"
    sx, sy = info.dx - obj.anchor_offset[0], info.dy - obj.anchor_offset[1]
    polys = [np.array(poly, dtype=np.float64) + (sx, sy) for poly in obj.polygons if len(poly)]
    if not polys:
        return None, "empty"
    fov = getattr(info, "fov", None)
    if fov is not None:
        cx, cy = np.concatenate(polys).mean(axis=0)
        if np.hypot(cx - fov.cx, cy - fov.cy) > fov.radius * IN_VIEW_RADIUS:
            return None, "out of view"
    return polys, ""


def compact_size(size: dict | None) -> str:
    """"~12 um" or "~12 x 8 um" (overlay.ts sizeWords; the camera font has no µ or ×)."""
    if not size:
        return ""

    def r(v: float) -> str:
        return str(round(v / 10) * 10 if v >= 100 else round(v) if v >= 10 else round(v, 1))

    approx = "" if size.get("measured") else "~"
    length, width = size["lengthUm"], size["widthUm"]
    return f"{approx}{r(length)} um" if width > 0.8 * length else f"{approx}{r(length)} x {r(width)} um"


def scale_bar(um_per_px: float, width_px: int, share: float = 0.18) -> tuple[int, float] | None:
    """The round length (µm) whose bar is closest to `share` of the picture's width: (um, px)."""
    if not um_per_px or um_per_px <= 0:
        return None
    target = width_px * share
    um = min(NICE_UM, key=lambda u: abs(u / um_per_px - target))
    return um, um / um_per_px


def _text(image, text: str, org: tuple[int, int], scale: float, colour, background=INK) -> None:
    thick = max(1, round(scale * 2))
    (w, h), base = cv2.getTextSize(text, FONT, scale, thick)
    x, y = org
    x = int(min(max(2, x), max(2, image.shape[1] - w - 6)))
    y = int(min(max(h + 6, y), image.shape[0] - base - 4))
    if background is not None:
        overlay = image.copy()
        cv2.rectangle(overlay, (x - 4, y - h - 4), (x + w + 4, y + base + 2), background, -1)
        cv2.addWeighted(overlay, 0.75, image, 0.25, 0, dst=image)
    cv2.putText(image, text, (x, y), FONT, scale, colour, thick, cv2.LINE_AA)


def draw(image: np.ndarray, info, objects, *, reading=None, colour: str = "pink", labels: bool = True,
         size_of=None, frame_time: float | None = None, members: list | None = None, overlays: bool = True,
         region: tuple | None = None) -> Drawn:
    """The frame (BGR) cropped to the eyepiece (or to `region`, frame pixels: what a window showed), with the
    outlines, labels and scale bar drawn, and the shown population's members that are verified in place on
    this frame (`members`: each one's colour and polygons, already in this frame's pixels;
    population.Layer.drawable), as the studio draws them."""
    h, w = image.shape[:2]
    fov = getattr(info, "fov", None)
    if region is not None:
        x0, y0 = int(max(0, region[0])), int(max(0, region[1]))
        x1, y1 = int(min(w, max(x0 + 1, region[2]))), int(min(h, max(y0 + 1, region[3])))
    elif fov is not None:
        pad = fov.radius * 1.06
        x0, y0 = int(max(0, fov.cx - pad)), int(max(0, fov.cy - pad))
        x1, y1 = int(min(w, fov.cx + pad)), int(min(h, fov.cy + pad))
    else:
        x0, y0, x1, y1 = 0, 0, w, h
    out = np.ascontiguousarray(image[y0:y1, x0:x1].copy())
    drawn = Drawn(out, (x0, y0, x1, y1))
    if not overlays:  # provenance stays in the sidecar; every saved pixel is the decoded camera's
        return drawn
    k = max(0.45, (x1 - x0) / 1500)  # text and lines grow with the picture
    line = max(2, round(2.5 * k))
    main = OUTLINE_BGR.get(colour, OUTLINE_BGR["pink"])
    callouts = []
    for obj in objects:
        polys, why = placed(obj, info)
        if polys is None:
            drawn.left_out[why] = drawn.left_out.get(why, 0) + 1
            continue
        pts = [np.round(p - (x0, y0)).astype(np.int32).reshape(-1, 1, 2) for p in polys]
        unsure = obj.status == "unsure" and not obj.arm
        part = getattr(obj, "kind", "") == "part"
        # A part of a dissected object is drawn as a part, thin and pale inside its object's outline, never as a
        # second outline of the object (live 2026-10-11: a stoma's parts read as a doubled outline of it).
        c = PAPER if part else IODINE if obj.status == "looking" else main
        if not part:
            fill = out.copy()
            cv2.fillPoly(fill, pts, c, cv2.LINE_AA)
            cv2.addWeighted(fill, 0.12, out, 0.88, 0, dst=out)
        outline(out, pts, c, max(1, line // 2) if part else line, casing=not part)  # a dark casing: seen on any stain
        name = obj.label or (f"Outline {obj.arm}" if obj.arm else "")
        if unsure and name:
            name += " (not sure)"
        size = size_of(obj) if size_of is not None else None
        if size and name:
            name += f", {compact_size(size)}"
        if labels and name:
            every = np.concatenate(pts).reshape(-1, 2)
            callouts.append((name, int(every[:, 0].mean()), int(every[:, 1].min()), c))
        drawn.outlines.append({"id": obj.id, "label": obj.label, "status": obj.status, "kind": obj.kind,
                               "size": size_of(obj) if size_of is not None else None})
    for colour, polys in members or []:  # each member in its own colour, from its id (contract 10)
        pts = [np.round(np.asarray(p, float) - (x0, y0)).astype(np.int32).reshape(-1, 1, 2) for p in polys]
        outline(out, pts, colour, max(1, line // 2))
    if members:
        drawn.outlines.append({"population": True, "members": len(members)})
    for name, cx, top, c in callouts:  # above the outline, clear of its line
        (tw, _), base = cv2.getTextSize(name, FONT, 0.6 * k, max(1, round(1.2 * k)))
        _text(out, name, (cx - tw // 2, top - line - base - 6), 0.6 * k, c)
    oh, ow = out.shape[:2]
    if reading is not None:
        bar = scale_bar(reading.um_per_px, ow)
        if bar is not None:
            um, px = bar
            bx1, by = ow - int(24 * k), oh - int(48 * k)
            bx0 = int(bx1 - px)
            cv2.line(out, (bx0, by), (bx1, by), INK, line + 4, cv2.LINE_AA)
            cv2.line(out, (bx0, by), (bx1, by), (255, 255, 255), line, cv2.LINE_AA)
            words = f"{'' if reading.measured else '~'}{um} um"
            (tw, _), _ = cv2.getTextSize(words, FONT, 0.55 * k, max(1, round(1.1 * k)))
            _text(out, words, ((bx0 + bx1) // 2 - tw // 2, by - int(10 * k)), 0.55 * k, (255, 255, 255))
            drawn.scale = {"um": um, "umPerPx": reading.um_per_px, "objective": reading.objective,
                           "measured": reading.measured}
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(frame_time or time.time()))
    _text(out, f"Real camera frame {info.id}, {when}. Outlines, labels and scale drawn by the app.",
          (int(8 * k), oh - int(10 * k)), 0.42 * k, PAPER)
    return drawn
