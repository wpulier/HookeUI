"""The Stage's still (S5a; orchestrator/still_fit.py): where the camera bridge's FFmpeg puts a still picture in its frames,
so a population found on the picture's original pixels is drawn on the Stage in the right place. The fit is checked
against FFmpeg itself, through the bridge's own filter chain, where FFmpeg is installed."""
import io
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest

from orchestrator import still_fit
from orchestrator.still_fit import Layout

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments" / "iphone-camera-feed" / "scripts"))
from simulated_camera import SimulatedCamera  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def bridge_chain(size: str) -> str:
    """The filter chain the bridge plays a still picture with (the user's assets and test footage alike)."""
    camera = SimulatedCamera.__new__(SimulatedCamera)
    camera.lock, camera.degradation, camera.still = threading.RLock(), {}, True
    return camera.filter_chain(30, size)


def test_the_fit_mirrors_the_bridges_chain():
    # still_fit mirrors exactly this chain: a change to it must change the fit (and this test) too.
    assert bridge_chain("1280x720") == ("fps=30,scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                                        "pad=1280:720:(ow-iw)/2:(oh-ih)/2")


@pytest.mark.parametrize("picture, frame, size", [
    ((96, 96), (320, 180), (180, 180)),
    ((333, 257), (1920, 1080), (1400, 1080)),  # each side to the nearest even size, then no more than the frame's
    ((1281, 721), (1280, 720), (1280, 720)),
    ((777, 333), (1280, 720), (1280, 548)),
    ((101, 999), (1280, 720), (72, 720)),
])
def test_the_fitted_size_is_ffmpegs(picture, frame, size):
    assert still_fit.fitted_size(*picture, *frame) == size


def test_a_letterboxed_still_scales_and_centres_by_pixel_centres():
    fit = still_fit.fit(Layout(96, 96), 320, 180)
    assert fit.frame == (320, 180) and fit.rect == (70, 0, 180, 180)
    assert fit.matrix == (1.875, 0.0, 70.4375, 0.0, 1.875, 0.4375)
    # the picture's first and last pixels' centres land on the fitted area's first and last
    assert fit.to_frame(-0.5, -0.5) == pytest.approx((69.5, -0.5))
    assert fit.to_frame(95.5, 95.5) == pytest.approx((249.5, 179.5))
    # a 4:2:0 JPEG's offset rounds down to even, as FFmpeg's pad does
    assert still_fit.fit(Layout(99, 97, 1, (2, 2)), 1280, 720).rect[0] == 272
    assert still_fit.fit(Layout(99, 97, 1, (1, 1)), 1280, 720).rect[0] == 273


@pytest.mark.parametrize("orientation", range(1, 9))
def test_every_orientation_maps_there_and_back(orientation):
    fit = still_fit.fit(Layout(300, 100, orientation), 1280, 720)
    assert fit.rect[2:] == ((240, 720) if orientation >= 5 else (1280, 426))
    for x, y in [(0, 0), (299, 0), (0, 99), (150.25, 40.5)]:
        assert fit.to_source(*fit.to_frame(x, y)) == pytest.approx((x, y))
    # the picture's corners land on the fitted area's edges, wherever the orientation puts them
    corners = np.array([fit.to_frame(x, y) for x, y in [(-0.5, -0.5), (299.5, -0.5), (-0.5, 99.5), (299.5, 99.5)]])
    x0, y0, w, h = fit.rect
    assert corners[:, 0].min() == pytest.approx(x0 - 0.5) and corners[:, 0].max() == pytest.approx(x0 + w - 0.5)
    assert corners[:, 1].min() == pytest.approx(y0 - 0.5) and corners[:, 1].max() == pytest.approx(y0 + h - 0.5)


def test_nothing_to_fit_has_no_fit():
    assert still_fit.fit(Layout(0, 10), 1280, 720) is None
    assert still_fit.fit(Layout(10, 10, 9), 1280, 720) is None


def _picture(kind: str, w: int = 99, h: int = 61, orientation: int = 1) -> bytes:
    from PIL import Image

    image = Image.fromarray(np.full((h, w, 3), 128, np.uint8) if kind != "jpg-gray" else np.full((h, w), 128, np.uint8))
    exif = image.getexif()
    if orientation != 1:
        exif[0x0112] = orientation
    out = io.BytesIO()
    if kind == "png":
        image.save(out, "PNG", exif=exif.tobytes())
    else:
        image.save(out, "JPEG", exif=exif.tobytes(), quality=90,
                   **({"subsampling": {"jpg-420": 2, "jpg-422": 1, "jpg-444": 0}[kind]} if kind != "jpg-gray" else {}))
    return out.getvalue()


@pytest.mark.parametrize("kind, sub", [("png", (1, 1)), ("jpg-420", (2, 2)), ("jpg-422", (2, 1)), ("jpg-444", (1, 1)),
                                       ("jpg-gray", (1, 1))])
def test_a_pictures_layout_is_read_from_its_head(kind, sub):
    assert still_fit.layout_of(_picture(kind)) == Layout(99, 61, 1, sub)


def test_a_jpegs_exif_orientation_turns_it_and_a_pngs_only_where_ffmpeg_turns_pngs(monkeypatch):
    # FFmpeg turns a JPEG by its EXIF orientation; a PNG by its eXIf only after 7.1 (CI's FFmpeg on the 0.4.0 candidate
    # did, the app's 7.1 does not), so its layout follows what the FFmpeg in use does (checked below against it).
    assert still_fit.layout_of(_picture("jpg-420", orientation=6)).orientation == 6
    monkeypatch.setattr(still_fit, "ffmpeg_turns_png_exif", lambda: False)
    assert still_fit.layout_of(_picture("png", orientation=6)).orientation == 1
    monkeypatch.setattr(still_fit, "ffmpeg_turns_png_exif", lambda: True)
    assert still_fit.layout_of(_picture("png", orientation=6)).orientation == 6
    assert still_fit.layout_of(_picture("png", orientation=1)).orientation == 1
    assert still_fit.layout_of(b"GIF89a....") is None


def test_the_probe_png_is_a_real_tagged_png_and_ffmpeg_answers_it():
    """The one-time question to FFmpeg uses a PNG it decodes, tagged a quarter turn; the answer is a yes or a no."""
    probe = still_fit._tagged_png()
    assert still_fit._png_exif_orientation(probe) == 6
    assert struct.unpack(">II", probe[16:24]) == (4, 2)
    if FFMPEG is not None:
        out = subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-f", "png_pipe", "-i", "pipe:0", "-frames:v", "1",
                              "-f", "image2pipe", "-vcodec", "png", "pipe:1"], input=probe, capture_output=True, check=True).stdout
        assert struct.unpack(">II", out[16:24]) in ((4, 2), (2, 4))
        still_fit.ffmpeg_turns_png_exif.cache_clear()
        assert still_fit.ffmpeg_turns_png_exif() == (struct.unpack(">II", out[16:24]) == (2, 4))
    assert still_fit.layout_of(b"\xff\xd8\xff\xe0\x00") is None  # a JPEG cut short has no layout


def _through_the_bridge(path: Path, size: str) -> np.ndarray:
    """One frame of the still as the bridge plays it: its own chain, input and output settings."""
    width, height = (int(v) for v in size.split("x"))
    out = subprocess.run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", "30",
                          "-i", str(path), "-an", "-vf", bridge_chain(size), "-frames:v", "1", "-q:v", "2",
                          "-vcodec", "mjpeg", "-f", "image2pipe", "pipe:1"], capture_output=True, check=True).stdout
    frame = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert frame.shape == (height, width)
    return frame


def _dotted(w: int, h: int) -> tuple[np.ndarray, list]:
    picture = np.full((h, w, 3), 30, np.uint8)
    dots = [(int(w * .2), int(h * .3)), (int(w * .7), int(h * .25)), (int(w * .45), int(h * .8))]
    for x, y in dots:
        cv2.circle(picture, (x, y), max(2, min(w, h) // 25), (255, 255, 255), -1)
    return picture, dots


@pytest.mark.skipif(FFMPEG is None, reason="FFmpeg is not installed here")
@pytest.mark.parametrize("kind, picture, orientation, size", [
    ("png", (333, 257), 1, "1920x1080"), ("png", (99, 61), 1, "1280x720"), ("png", (1000, 1600), 6, "1280x720"),
    ("jpg-420", (99, 97), 1, "1280x720"), ("jpg-420", (777, 333), 1, "640x480"), ("jpg-422", (333, 257), 1, "640x480"),
    ("jpg-444", (1281, 721), 1, "1920x1080"), ("jpg-420", (640, 480), 6, "1280x720"), ("jpg-420", (333, 257), 8, "1280x720"),
    ("jpg-444", (300, 200), 3, "1280x720"), ("jpg-420", (300, 200), 5, "640x480"), ("jpg-420", (300, 200), 7, "1280x720"),
    ("jpg-444", (300, 200), 2, "1280x720"), ("jpg-420", (300, 200), 4, "640x480"),
])
def test_the_fit_is_where_ffmpeg_puts_the_picture(tmp_path, kind, picture, orientation, size):
    """Three dots drawn on the picture, found in the bridge's frame where the fit puts their centres (within a pixel),
    and the picture's place (the letterbox) where the fit says, for PNGs and JPEGs of every subsampling, turned by their
    EXIF orientation (a PNG's eXIf as the FFmpeg installed here does: 7.1 shows it as encoded, later versions turn it)."""
    from PIL import Image

    pixels, dots = _dotted(*picture)
    image = Image.fromarray(pixels[:, :, ::-1])
    exif = image.getexif()
    exif[0x0112] = orientation
    path = tmp_path / f"still.{kind[:3]}"
    if kind == "png":
        image.save(path, "PNG", exif=exif.tobytes())
    else:
        image.save(path, "JPEG", exif=exif.tobytes(), quality=95, subsampling={"jpg-420": 2, "jpg-422": 1, "jpg-444": 0}[kind])
    layout = still_fit.read_layout(path)
    width, height = (int(v) for v in size.split("x"))
    fit = still_fit.fit(layout, width, height)
    frame = _through_the_bridge(path, size)
    ys, xs = np.nonzero(frame > 12)
    seen = (int(xs.min()), int(ys.min()), int(xs.max() + 1 - xs.min()), int(ys.max() + 1 - ys.min()))
    assert all(abs(a - b) <= 1 for a, b in zip(seen, fit.rect)), (seen, fit.rect)
    for x, y in dots:
        fx, fy = fit.to_frame(x, y)
        reach = 4 + 3 * max(2, min(picture) // 25) * max(fit.rect[2] / layout.turned[0], 0.5)
        x0, y0 = int(max(0, fx - reach)), int(max(0, fy - reach))
        patch = frame[y0:int(fy + reach) + 1, x0:int(fx + reach) + 1] > 140
        py, px = np.nonzero(patch)
        assert np.hypot(px.mean() + x0 - fx, py.mean() + y0 - fy) < 1.0, (x, y, fx, fy)


@pytest.mark.skipif(FFMPEG is None, reason="FFmpeg is not installed here")
def test_a_frame_shows_the_still_only_when_its_picture_is_the_stills(tmp_path):
    """The layer's check (population.Layer._check_still): the bridge's frame of the still matches its reference; another
    picture, or the same one off by 8 px, does not."""
    rng = np.random.default_rng(3)
    texture = cv2.GaussianBlur(rng.integers(0, 255, (257, 333), dtype=np.uint8), (0, 0), 3)
    other = cv2.GaussianBlur(rng.integers(0, 255, (257, 333), dtype=np.uint8), (0, 0), 3)
    for name, pixels in (("still.png", texture), ("other.png", other)):
        cv2.imwrite(str(tmp_path / name), pixels)
    fit = still_fit.fit(still_fit.read_layout(tmp_path / "still.png"), 1280, 720)
    ref = still_fit.reference(texture, fit)
    shown = _through_the_bridge(tmp_path / "still.png", "1280x720")
    assert still_fit.score(ref, shown) >= 0.97
    assert still_fit.score(ref, _through_the_bridge(tmp_path / "other.png", "1280x720")) < 0.5
    assert still_fit.score(ref, np.roll(shown, 8, axis=1)) < still_fit.STILL_SCORE
    assert still_fit.score(ref, shown[:, :640]) == 0.0  # another frame size is not this fit's


def test_even_pictures_match_only_even_pictures_of_their_grey():
    flat = np.full((96, 96), 200, np.uint8)
    fit = still_fit.fit(Layout(96, 96), 320, 180)
    ref = still_fit.reference(flat, fit)
    frame = np.zeros((180, 320), np.uint8)
    frame[:, 70:250] = 204
    assert still_fit.score(ref, frame) == 1.0
    frame[:, 70:250] = 120
    assert still_fit.score(ref, frame) == 0.0
    patterned = np.full((96, 96), 220, np.uint8)
    cv2.circle(patterned, (25, 25), 10, 40, -1)
    assert still_fit.score(still_fit.reference(patterned, fit), np.full((180, 320), 210, np.uint8)) == 0.0
