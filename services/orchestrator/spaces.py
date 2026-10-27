"""Coordinate spaces: the one place the vocabulary and the mappings are written (contract 10 and invariant 14
of docs/design/2026-10-06-captures-operations-evidence.md; the studio's twin is apps/studio/src/spaces.ts).

Every overlay, close-up and mark states which space its coordinates are in:

- **frame**: live camera-frame pixels. A thing is stored at the pixels of an anchor frame (a stated epoch and
  slide offset) and drawn at `p + (offset_now - offset_anchor)`. Slide px are frame px of a frame at offset
  (0, 0).
- **capture**: the pixels of one capture's image (contract 1). An enlarged view is a crop and a scale of it
  (`Crop` of coords.py, `scale` = view px per capture px). It is placed on the live view only where the
  server verified it (invariant 12); otherwise it is shown on its capture. A still's original (an imported picture's
  own pixels) is a capture too; while the Stage shows that still, the bridge's frames hold it fitted and letterboxed,
  and its pixels map to theirs by the still's fit (still_fit.py: `still_to_frame`, an affine `matrix`).
- **workspace**: the studio's CSS pixels, from its screen report (the rects are always these).
- **map**: the slide map's pixels: slide px x `slidemap.SCALE`, minus the picture's `origin`.
- **picture**: a picture's own coordinates where nothing relates them to the live view (a researcher's or
  generated picture, a result viewer's page). Not a mapping to anything: it says so. Its geometry must also
  state its `unit` (PICTURE_UNITS): "px" or "fraction" (0-1 of the picture's width and height, as a
  researcher's annotated-picture boxes are); `picture_to_px` maps a fraction to px.

Pen marks are not built (the UX's pen). When they are, they state their space here too and reach models only
as prompts on the clean capture (invariant 14).
"""
from __future__ import annotations

from .coords import Crop

FRAME, CAPTURE, WORKSPACE, MAP, PICTURE = "frame", "capture", "workspace", "map", "picture"
SPACES = (FRAME, CAPTURE, WORKSPACE, MAP, PICTURE)
PICTURE_UNITS = ("px", "fraction")

# What the server sends the studio that carries or places geometry, and the space it states (`space` on the
# message). tests/test_spaces.py builds each and fails on one that does not state its space.
#   scene       outlines: polygons in the pixels of their anchor frame (anchorFrame, anchorEpoch, anchorOffset)
#   population  members' polygons in their capture's pixels (`capture`, `epoch`, `offset`)
#   layer       the population layer's placement: epoch, offset and correction are live-frame terms; `fit` (a population
#               found on the Stage's still) maps the still's original pixels to frame px
#   motion-tracks  track points in their capture's view pixels (`capture`)
#   tracks-layer   as layer
#   trails      a followed outline's places in slide px (frame px at offset (0, 0)), per segment epoch
#   candidates  the detector's blobs (x, y, r) in the pixels of frame `frameId`
#   magnifier   `center` in the pixels of the object's anchor frame; each source says its own
#   slidemap    the map picture, `origin` and `size` in map px
WIRES = {"scene": FRAME, "population": CAPTURE, "layer": FRAME, "motion-tracks": CAPTURE, "tracks-layer": FRAME,
         "trails": FRAME, "magnifier": FRAME, "slidemap": MAP, "candidates": FRAME}
# A magnifier source's pixels: the live view's frame, the sharpest recent frame's still (a crop of it), a picture.
SOURCE_SPACES = {"live": FRAME, "held": CAPTURE, "image": PICTURE}

# What each presentation kind (screen.TAKES) shows, in whose pixels (`space` on the studio's screen report item;
# its `rect` is always the workspace). The magnifier's depends on its source (SOURCE_SPACES).
# A kind not listed (an index "entry", anything new) is the workspace's: the studio's own default (itemSpace).
PRESENTATION_SPACES = {"live": FRAME, "population": CAPTURE, "tracks": CAPTURE, "outlines": FRAME, "magnifier": None,
                       "viewer": PICTURE, "slidemap": MAP, "card": WORKSPACE, "made-today": WORKSPACE, "entry": WORKSPACE,
                       "sidebar": WORKSPACE, "sessions": WORKSPACE, "document": WORKSPACE}


def valid(space: object) -> bool:
    return isinstance(space, str) and space in SPACES


def of_wire(kind: str) -> str:
    return WIRES[kind]


def source_space(kind: object) -> str:
    return SOURCE_SPACES.get(str(kind), PICTURE)


def item_space(kind: str, source: object = None) -> str:
    """The space a screen item's contents are in: its kind's, or for the magnifier its source's (the studio
    reports "live", "held", or a picture's URL; closed: nothing shown, so the live frame it opens on)."""
    if kind == "magnifier":
        return SOURCE_SPACES["live"] if not source else SOURCE_SPACES.get(str(source), PICTURE)
    return PRESENTATION_SPACES.get(kind) or WORKSPACE


# --- the mappings -----------------------------------------------------------------------------------------
Point = tuple[float, float]


def frame_to_frame(p: Point, anchor_offset: Point, now_offset: Point) -> Point:
    """A place stored at an anchor frame's pixels, in a frame with offset `now_offset` (the same epoch)."""
    return p[0] + now_offset[0] - anchor_offset[0], p[1] + now_offset[1] - anchor_offset[1]


def capture_to_frame(p: Point, capture_offset: Point, now_offset: Point, correction: Point = (0.0, 0.0)) -> Point:
    """A capture's pixel in the live frame now: a capture is a frame as it was, so by the offsets, plus the
    layer's own-pixel correction (population.Layer.correction)."""
    x, y = frame_to_frame(p, capture_offset, now_offset)
    return x + correction[0], y + correction[1]


def still_to_frame(p: Point, matrix) -> Point:
    """A still's original pixel in the bridge's frames while the Stage shows it: its fit's `matrix` (a, b, c, d, e, f),
    (a x + b y + c, d x + e y + f): the still's orientation, its scale to the fitted size and the letterbox."""
    a, b, c, d, e, f = matrix
    return a * p[0] + b * p[1] + c, d * p[0] + e * p[1] + f


def frame_to_still(p: Point, matrix) -> Point:
    a, b, c, d, e, f = matrix
    det = a * e - b * d
    x, y = p[0] - c, p[1] - f
    return (e * x - b * y) / det, (a * y - d * x) / det


def frame_to_slide(p: Point, offset: Point) -> Point:
    return p[0] - offset[0], p[1] - offset[1]


def slide_to_frame(p: Point, offset: Point) -> Point:
    return p[0] + offset[0], p[1] + offset[1]


def slide_to_map(p: Point, scale: float, origin: Point) -> Point:
    """Map picture px of slide px `p` (slide px x scale, less the picture's origin; slidemap.py)."""
    return p[0] * scale - origin[0], p[1] * scale - origin[1]


def map_to_slide(p: Point, scale: float, origin: Point) -> Point:
    return (p[0] + origin[0]) / scale, (p[1] + origin[1]) / scale


def frame_to_map(p: Point, offset: Point, scale: float, origin: Point) -> Point:
    return slide_to_map(frame_to_slide(p, offset), scale, origin)


def view_to_capture(p: Point, crop: Crop, scale: float) -> Point:
    """An enlarged view's pixel to its capture's pixel: the view is `crop` of the capture drawn at `scale`."""
    return crop.to_frame(p[0] / scale, p[1] / scale)


def capture_to_view(p: Point, crop: Crop, scale: float) -> Point:
    return (p[0] - crop.x) * scale, (p[1] - crop.y) * scale


def frame_to_workspace(p: Point, scale: float, ox: float, oy: float, canvas: Point = (0.0, 0.0)) -> Point:
    """A live-frame pixel in the workspace: the studio places the frame on its canvas at canvas px
    `ox + p * scale` (view.ts `placeFrame`: the layout for a wide stage, the side column open, a phone), and the
    canvas sits at `canvas` in the workspace."""
    return canvas[0] + ox + p[0] * scale, canvas[1] + oy + p[1] * scale


def workspace_to_frame(p: Point, scale: float, ox: float, oy: float, canvas: Point = (0.0, 0.0)) -> Point:
    return (p[0] - canvas[0] - ox) / scale, (p[1] - canvas[1] - oy) / scale


def frame_to_magnifier(p: Point, x0: float, y0: float, k: float, panel: Point = (0.0, 0.0)) -> Point:
    """A live-frame pixel in the workspace through a magnifier showing the frame window whose top-left is
    (x0, y0) at `k` panel px per frame px (view.ts `magnifierWindow`; a zoomed magnifier has a smaller window)."""
    return panel[0] + (p[0] - x0) * k, panel[1] + (p[1] - y0) * k


def magnifier_to_frame(p: Point, x0: float, y0: float, k: float, panel: Point = (0.0, 0.0)) -> Point:
    return (p[0] - panel[0]) / k + x0, (p[1] - panel[1]) / k + y0


def picture_to_px(p: Point, size: Point) -> Point:
    """A picture's fraction coordinates (0-1) in its pixels, `size` = (width, height)."""
    return p[0] * size[0], p[1] * size[1]


def px_to_picture(p: Point, size: Point) -> Point:
    return p[0] / size[0], p[1] / size[1]
