"""Where the camera bridge puts a still picture in its frames: the Stage's still, and the fit that maps a population
found on the picture's original pixels onto the Stage (docs/architecture/freshness-and-overlay.md, "A population found on
the Stage's still").

The bridge plays a picture (the user's still assets and test footage alike: `simulated_camera.py` `filter_chain`, the one
path the real bridge and the sim share) through FFmpeg, which first turns it by its EXIF orientation (FFmpeg's
autorotate) and then fits it into the bridge's frame size:

    scale=W:H:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=W:H:(ow-iw)/2:(oh-ih)/2

A population found on the original (`source_images`) is in the picture's encoded grid ("EXIF rotation not applied"), so
its place in a frame is the fit: the orientation, the scale to the fitted size, and the letterbox. `fit` mirrors FFmpeg
(7.1 in the app, `packaging/macos/ffmpeg.sh`; a PNG's eXIf orientation is applied only when the FFmpeg the bridge runs
turns PNGs by it, which later versions do: `ffmpeg_turns_png_exif`), `tests/test_still_fit.py` checks it against the
FFmpeg installed here, and the layer
checks the frames it draws on by their pixels as well (`population.Layer.check`), so a fit that is wrong draws nothing.

Coordinates are pixel indices, a pixel's index being its centre (as in a contour: `evidence.polygons_of`), in both the
picture's grid and the frame.
"""
from __future__ import annotations

import functools
import shutil
import struct
import subprocess
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from . import spaces

# How much of a picture file is read for its layout: its size, orientation and chroma subsampling all come before its
# pixels (a JPEG's EXIF and frame header, a PNG's IHDR), and an EXIF block is at most 64 KiB.
HEAD_BYTES = 1 << 20

# FFmpeg's autorotate for a JPEG's EXIF orientation (1: as encoded). Each maps a place (u, v) in the encoded
# picture, continuous (a pixel spans [i, i + 1)), w x h, to the turned picture; the turned picture is h x w for 5 to 8.
_TURNS = {
    1: lambda u, v, w, h: (u, v),
    2: lambda u, v, w, h: (w - u, v),
    3: lambda u, v, w, h: (w - u, h - v),
    4: lambda u, v, w, h: (u, h - v),
    5: lambda u, v, w, h: (v, u),
    6: lambda u, v, w, h: (h - v, u),
    7: lambda u, v, w, h: (h - v, w - u),
    8: lambda u, v, w, h: (v, w - u),
}


@dataclass(frozen=True)
class Layout:
    """What FFmpeg reads of a picture file before it fits it: its encoded size, its EXIF orientation and the chroma
    subsampling of its decoded pixels (a JPEG's 4:2:0 is (2, 2)): FFmpeg's pad rounds its offset down to that."""
    width: int
    height: int
    orientation: int = 1
    sub: tuple[int, int] = (1, 1)

    @property
    def turned(self) -> tuple[int, int]:
        """The picture's size as FFmpeg fits it, after its orientation."""
        return (self.height, self.width) if self.orientation >= 5 else (self.width, self.height)


@dataclass(frozen=True)
class Fit:
    """A still's place in the bridge's frames: `frame` (the frame size it was made for), `rect` (x, y, width, height of
    the picture in the frame) and `matrix` (a, b, c, d, e, f: a pixel (x, y) of the picture's encoded grid is at
    (a x + b y + c, d x + e y + f) in the frame)."""
    frame: tuple[int, int]
    rect: tuple[int, int, int, int]
    matrix: tuple[float, float, float, float, float, float]
    orientation: int = 1

    def to_frame(self, x: float, y: float) -> tuple[float, float]:
        return spaces.still_to_frame((x, y), self.matrix)

    def to_source(self, fx: float, fy: float) -> tuple[float, float]:
        return spaces.frame_to_still((fx, fy), self.matrix)

    def wire(self) -> dict:
        return {"frame": list(self.frame), "rect": list(self.rect), "matrix": [round(v, 6) for v in self.matrix],
                "orientation": self.orientation}


def _rescale(a: int, b: int, c: int) -> int:
    """FFmpeg's av_rescale: a * b / c to the nearest integer, halves away from zero (all positive here)."""
    return (a * b + c // 2) // c


def fitted_size(width: int, height: int, frame_w: int, frame_h: int, divisible: int = 2) -> tuple[int, int]:
    """FFmpeg 7.1's `force_original_aspect_ratio=decrease:force_divisible_by=2` (libavfilter/scale_eval.c): each side
    of the aspect-kept size to the nearest multiple of `divisible`, no more than the frame's, then down to a multiple."""
    w = min(_rescale(frame_h, width, height * divisible) * divisible, frame_w)
    h = min(_rescale(frame_w, height, width * divisible) * divisible, frame_h)
    return w - w % divisible, h - h % divisible


def fit(layout: Layout, frame_w: int, frame_h: int) -> Fit | None:
    """The picture's place in a frame of `frame_w` x `frame_h`, as the bridge's FFmpeg puts it; None for a picture or
    frame with no size, or an orientation FFmpeg turns by other than a quarter turn or a mirror."""
    if min(layout.width, layout.height, frame_w, frame_h) <= 0 or layout.orientation not in _TURNS:
        return None
    tw, th = layout.turned
    sw, sh = fitted_size(tw, th, frame_w, frame_h)
    if sw <= 0 or sh <= 0:
        return None
    # pad's x and y: (ow - iw) / 2, truncated, then down to the decoded pixels' chroma subsampling (vf_pad.c).
    ox, oy = (frame_w - sw) // 2, (frame_h - sh) // 2
    ox, oy = ox - ox % layout.sub[0], oy - oy % layout.sub[1]
    sx, sy = sw / tw, sh / th
    turn = _TURNS[layout.orientation]
    w, h = layout.width, layout.height

    def place(x: float, y: float) -> tuple[float, float]:
        u, v = turn(x + 0.5, y + 0.5, w, h)  # a pixel's centre, continuous
        return u * sx + ox - 0.5, v * sy + oy - 0.5  # back to the frame's pixel index

    (c, f), (x1, y1), (x0, y0) = place(0, 0), place(1, 0), place(0, 1)
    matrix = (x1 - c, x0 - c, c, y1 - f, y0 - f, f)
    return Fit((frame_w, frame_h), (ox, oy, sw, sh), tuple(float(v) for v in matrix), layout.orientation)


# --- reading a picture's layout ---------------------------------------------------------------------------------------
def layout_of(data: bytes) -> Layout | None:
    """A PNG's or JPEG's layout from its first bytes (`HEAD_BYTES` hold it), or None for anything else."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _png(data)
    if data[:2] == b"\xff\xd8":
        return _jpeg(data)
    return None


def read_layout(path: str | Path) -> Layout | None:
    """The layout of the picture file the bridge plays (the Stage's still)."""
    try:
        with open(path, "rb") as stream:
            return layout_of(stream.read(HEAD_BYTES))
    except OSError:
        return None


def _png(data: bytes) -> Layout | None:
    """A PNG's size, and its eXIf orientation when the FFmpeg the bridge runs turns a PNG by it (FFmpeg 7.1, in the app,
    shows a PNG as encoded; later versions turn it, as CI's did on the 0.4.0 candidate: `ffmpeg_turns_png_exif`)."""
    if len(data) < 33 or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    orientation = (_png_exif_orientation(data) or 1) if ffmpeg_turns_png_exif() else 1
    return Layout(width, height, orientation, (1, 1))


def _png_exif_orientation(data: bytes) -> int | None:
    """The orientation in a PNG's eXIf chunk (before its pixels), or None."""
    at = 8
    while at + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[at:at + 8])
        if kind == b"eXIf":
            return _exif_orientation(data[at + 8:at + 8 + length])
        if kind in (b"IDAT", b"IEND"):
            return None
        at += 12 + length
    return None


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def _tagged_png(width: int = 4, height: int = 2, orientation: int = 6) -> bytes:
    """A small grey PNG whose eXIf says `orientation` (a TIFF block with one tag, 0x0112)."""
    tiff = b"MM\x00\x2a" + struct.pack(">IH", 8, 1) + struct.pack(">HHIHH", 0x0112, 3, 1, orientation, 0) + struct.pack(">I", 0)
    rows = b"".join(b"\x00" + bytes(range(0, 255, 255 // width))[:width] for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + _chunk(b"eXIf", tiff) + _chunk(b"IDAT", zlib.compress(rows)) + _chunk(b"IEND", b""))


@functools.lru_cache(maxsize=1)
def ffmpeg_turns_png_exif() -> bool:
    """Whether the FFmpeg the bridge runs (the first on PATH: bridge_common.ffmpeg_path) turns a PNG by its eXIf
    orientation. Asked once per process (layouts are read in a thread, never on the event loop): a 4x2 PNG tagged 6, a
    quarter turn, comes back 2x4 when it does. Without an FFmpeg that answers, as FFmpeg 7.1 (it does not)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="hooke-png-turn-") as folder:
            probe = Path(folder) / "tagged.png"
            probe.write_bytes(_tagged_png())
            # The bridge's own input (simulated_camera.py: a still looped as a 30 fps stream), one frame out as a PNG.
            out = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", "30",
                                  "-i", str(probe), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
                                 capture_output=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    if out[:8] != b"\x89PNG\r\n\x1a\n" or out[12:16] != b"IHDR":
        return False
    return struct.unpack(">II", out[16:24]) == (2, 4)


def _jpeg(data: bytes) -> Layout | None:
    orientation, offset = 1, 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            return None
        marker = data[offset + 1]
        if marker == 0xFF:
            offset += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        length = int.from_bytes(data[offset + 2:offset + 4], "big")
        body = data[offset + 4:offset + 2 + length]
        if marker == 0xE1 and body[:6] == b"Exif\x00\x00":
            orientation = _exif_orientation(body[6:]) or orientation
        if marker in (0xC0, 0xC1, 0xC2) and len(body) >= 6:
            height, width, count = struct.unpack(">HHB", body[1:6])
            factors = [(body[7 + 3 * i] >> 4, body[7 + 3 * i] & 15) for i in range(count) if len(body) >= 9 + 3 * i]
            if not factors:
                return None
            hs, vs = [f[0] for f in factors if f[0]], [f[1] for f in factors if f[1]]
            sub = (max(hs) // min(hs), max(vs) // min(vs)) if hs and vs else (1, 1)
            return Layout(width, height, orientation, sub)
        if marker in (0xDA, 0xD9):
            return None
        offset += 2 + length
    return None


def _exif_orientation(tiff: bytes) -> int | None:
    """Tag 0x0112 of a TIFF block's first IFD (EXIF), 1 to 8, or None."""
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return None
    end = "<" if tiff[:2] == b"II" else ">"
    ifd = struct.unpack(end + "I", tiff[4:8])[0]
    if ifd + 2 > len(tiff):
        return None
    count = struct.unpack(end + "H", tiff[ifd:ifd + 2])[0]
    for i in range(count):
        at = ifd + 2 + 12 * i
        if at + 12 > len(tiff):
            return None
        tag, kind = struct.unpack(end + "HH", tiff[at:at + 4])
        if tag == 0x0112 and kind == 3:
            value = struct.unpack(end + "H", tiff[at + 8:at + 10])[0]
            return value if value in _TURNS else None
    return None


# --- checking a frame shows the still ---------------------------------------------------------------------------------
# A frame shows the still when its picture, where the fit puts it, matches the still's own pixels fitted the same way:
# compared at 1/REDUCE of the frame's size by normalised correlation, at least STILL_SCORE (the same picture through
# FFmpeg's scaler and JPEG measured 0.97 to 1.0; another picture or the camera, far less: tests/test_still_fit.py).
REDUCE = 4
STILL_SCORE = 0.9
FLAT_STD = 2.0  # grey levels: a picture this even has no pattern to correlate...
FLAT_DIFF = 10.0  # ...and matches when its mean differs by no more than this


@dataclass(frozen=True)
class Reference:
    """The still fitted into a frame and reduced, cut to the picture's place (a margin inside it): what a frame showing
    it looks like there."""
    fit: Fit
    cut: tuple[int, int, int, int]  # x0, y0, x1, y1 in the reduced frame
    pixels: object  # uint8, the cut

    @property
    def nbytes(self) -> int:
        return int(getattr(self.pixels, "nbytes", 0))


def turn(gray, orientation: int):
    """The encoded picture as FFmpeg shows it, turned by its orientation (`_TURNS`)."""
    import cv2

    if orientation == 2:
        return cv2.flip(gray, 1)
    if orientation == 3:
        return cv2.flip(gray, -1)
    if orientation == 4:
        return cv2.flip(gray, 0)
    if orientation == 5:
        return gray.T.copy()
    if orientation == 6:
        return cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE)
    if orientation == 7:
        return cv2.flip(gray.T.copy(), -1)
    if orientation == 8:
        return cv2.rotate(gray, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return gray


def _reduced(gray):
    import cv2

    h, w = gray.shape[:2]
    return cv2.resize(gray, (max(1, w // REDUCE), max(1, h // REDUCE)), interpolation=cv2.INTER_AREA)


def reference(gray, fit_: Fit) -> Reference | None:
    """The reference a frame is checked against, from the still's original grey pixels (its encoded grid)."""
    import cv2
    import numpy as np

    shown = turn(gray, fit_.orientation)
    x, y, sw, sh = fit_.rect
    grow = sw > shown.shape[1] or sh > shown.shape[0]
    fitted = cv2.resize(shown, (sw, sh), interpolation=cv2.INTER_LINEAR if grow else cv2.INTER_AREA)
    canvas = np.zeros((fit_.frame[1], fit_.frame[0]), np.uint8)
    canvas[y:y + sh, x:x + sw] = fitted
    small = _reduced(canvas)
    # One reduced pixel inside the picture's place on each side: its edge blends with the letterbox differently in
    # FFmpeg's scaler and here.
    x0, y0 = -(-x // REDUCE) + 1, -(-y // REDUCE) + 1
    x1, y1 = (x + sw) // REDUCE - 1, (y + sh) // REDUCE - 1
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return Reference(fit_, (x0, y0, x1, y1), small[y0:y1, x0:x1].copy())


def score(ref: Reference, frame_gray) -> float:
    """How well a frame's grey picture matches the reference where the fit puts the still: 1 the same, 0 unrelated."""
    import cv2
    import numpy as np

    if frame_gray is None or frame_gray.shape[:2] != (ref.fit.frame[1], ref.fit.frame[0]):
        return 0.0
    x0, y0, x1, y1 = ref.cut
    seen = _reduced(frame_gray)[y0:y1, x0:x1]
    want = ref.pixels
    if seen.shape != want.shape:
        return 0.0
    flat_want, flat_seen = float(want.std()) < FLAT_STD, float(seen.std()) < FLAT_STD
    if flat_want or flat_seen:  # one even picture is another's only if both are even, of about the same grey
        return 1.0 if flat_want and flat_seen and abs(float(seen.mean()) - float(want.mean())) <= FLAT_DIFF else 0.0
    found = float(cv2.matchTemplate(seen, want, cv2.TM_CCOEFF_NORMED)[0, 0])
    return found if np.isfinite(found) else 0.0
