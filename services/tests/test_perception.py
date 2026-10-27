import cv2
import numpy as np
import pytest

from orchestrator.candidates import CandidateTracker, propose
from orchestrator.coords import Crop, frame_box_to_gemini, gemini_box_to_frame, gemini_point_to_frame
from orchestrator.fov import FovTracker, detect_fov, edge_radius
from orchestrator.motion import MotionTracker

W, H = 1280, 720


def texture(seed: int = 0, size: tuple[int, int] = (2000, 2000)) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = rng.random(size, dtype=np.float32)
    t = cv2.GaussianBlur(t, (0, 0), 3)
    t = cv2.normalize(t, None, 60, 230, cv2.NORM_MINMAX)
    return t.astype(np.uint8)


def eyepiece_frame(specimen: np.ndarray, dx: int, dy: int, cx=690, cy=395, r=390) -> np.ndarray:
    """Specimen texture shifted by (dx, dy) seen through a fixed circular eyepiece."""
    x0, y0 = 300 - dx, 300 - dy
    view = specimen[y0 : y0 + H, x0 : x0 + W].copy()
    mask = np.zeros((H, W), np.uint8)
    cv2.circle(mask, (cx, cy), r, 255, -1)
    view[mask == 0] = 4
    return view


# --- coords ---------------------------------------------------------------------------

def test_gemini_box_on_crop_maps_to_full_frame():
    crop = Crop(x=300, y=100, width=500, height=400)
    # [y0, x0, y1, x1] on 0-1000 of the crop
    assert gemini_box_to_frame([0, 0, 1000, 1000], crop) == (300, 100, 800, 500)
    assert gemini_box_to_frame([500, 500, 750, 600], crop) == (550, 300, 600, 400)


def test_gemini_point_is_y_then_x():
    crop = Crop.full(W, H)
    assert gemini_point_to_frame([500, 250], crop) == (320.0, 360.0)


def test_box_roundtrip():
    crop = Crop(x=200, y=50, width=800, height=600)
    box = (400.0, 200.0, 600.0, 350.0)
    assert gemini_box_to_frame(frame_box_to_gemini(box, crop), crop) == pytest.approx(box, abs=1.0)


# --- field of view ----------------------------------------------------------------------

def test_detects_eyepiece_disc():
    fov = detect_fov(eyepiece_frame(texture(), 0, 0))
    assert fov is not None
    assert fov.cx == pytest.approx(690, abs=6)
    assert fov.cy == pytest.approx(395, abs=6)
    assert fov.radius == pytest.approx(390, abs=8)


def test_disc_clipped_by_frame_edges_keeps_true_radius():
    fov = detect_fov(eyepiece_frame(texture(), 0, 0, cx=760, cy=386, r=635))
    assert fov is not None
    assert fov.radius == pytest.approx(635, rel=0.04)


def test_no_disc_in_plain_camera_view():
    assert detect_fov(texture()[:H, :W]) is None


def test_fov_tracker_ignores_jitter():
    tracker = FovTracker(every_n_frames=1)
    spec = texture()
    first = None
    for _ in range(4):  # it takes a few agreeing looks before the view zooms to an eyepiece
        first = tracker.update(eyepiece_frame(spec, 0, 0, r=390))
    assert first is not None
    for r in (393, 388, 392, 389):
        assert tracker.update(eyepiece_frame(spec, 0, 0, r=r)) == first


def test_a_dark_view_or_a_bright_blob_is_not_an_eyepiece():
    import cv2
    import numpy as np

    dark = np.full((720, 1280), 8, np.uint8)
    cv2.circle(dark, (640, 360), 300, 22, -1)  # a dim disc in a dark room
    assert detect_fov(dark) is None
    tracker = FovTracker(every_n_frames=1)
    spec = texture()
    assert tracker.update(eyepiece_frame(spec, 0, 0, r=390)) is None  # one find is not enough


def soft_disc_frame(specimen: np.ndarray, r: float, cx=690.25, cy=395.5, edge_sigma=1.2) -> np.ndarray:
    """An eyepiece view with a sub-pixel radius and a soft rim (the real rim is 3 px wide)."""
    view = specimen[300 : 300 + H, 300 : 300 + W].astype(np.float32)
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    inside = np.clip(r - np.hypot(xs - cx, ys - cy) + 0.5, 0.0, 1.0)  # area-weighted at the rim
    inside = cv2.GaussianBlur(inside, (0, 0), edge_sigma)
    return np.clip(view * inside + 4.0 * (1 - inside), 0, 255).astype(np.uint8)


def test_edge_radius_measures_the_disc_to_a_fraction_of_a_pixel():
    spec = texture()
    tracker_fov = detect_fov(soft_disc_frame(spec, 390.0))
    assert edge_radius(soft_disc_frame(spec, 390.0), tracker_fov) == pytest.approx(390.0, abs=0.15)
    a, b = (edge_radius(soft_disc_frame(spec, r), tracker_fov) for r in (390.0, 390.5))
    assert b - a == pytest.approx(0.5, abs=0.1)
    assert edge_radius(spec[:H, :W], None) is None


def test_the_knob_leaves_the_disc_alone_and_the_phones_lens_does_not():
    """Defocusing the specimen (the knob) leaves the rim where it was; the phone's lens refocusing
    scales everything it sees, the disc too (focus breathing: 1-2.4% on the 2026-10-06 demo)."""
    from orchestrator.coach import LENS_SHARE

    spec = texture()
    fov = detect_fov(soft_disc_frame(spec, 390.0))
    sharp = edge_radius(soft_disc_frame(spec, 390.0), fov)
    knob = edge_radius(soft_disc_frame(cv2.GaussianBlur(spec, (0, 0), 6), 390.0), fov)
    assert abs(knob - sharp) / sharp < LENS_SHARE / 5
    phone = edge_radius(soft_disc_frame(spec, 390.0 * 1.015, edge_sigma=2.0), fov)
    assert (phone - sharp) / sharp == pytest.approx(0.015, abs=0.001)


def test_edge_radius_of_a_disc_larger_than_the_frame():
    spec = texture()
    frame = soft_disc_frame(spec, 627.0, cx=640.0, cy=360.0)  # only its left and right rims show
    fov = detect_fov(frame)
    assert edge_radius(frame, fov) == pytest.approx(627.0, abs=0.3)


# --- motion -----------------------------------------------------------------------------

def test_motion_tracks_stage_translation_despite_fixed_surround():
    spec = texture(1)
    tracker = MotionTracker()
    fov = detect_fov(eyepiece_frame(spec, 0, 0))
    path = [(int(4 * i), int(-3 * i)) for i in range(40)]
    for frame_id, (dx, dy) in enumerate(path):
        tracker.update(frame_id, eyepiece_frame(spec, dx, dy), fov)
    final = tracker.offset(len(path) - 1)
    assert final == pytest.approx(path[-1], abs=2.0)


def test_map_point_follows_content():
    spec = texture(2)
    tracker = MotionTracker()
    fov = detect_fov(eyepiece_frame(spec, 0, 0))
    for frame_id, d in enumerate([(0, 0), (10, 5), (25, 12)]):
        tracker.update(frame_id, eyepiece_frame(spec, *d), fov)
    assert tracker.map_point(600, 400, 0, 2) == pytest.approx((625, 412), abs=2.0)


def test_motion_rejects_static_frames_as_zero_motion():
    spec = texture(3)
    tracker = MotionTracker()
    frame = eyepiece_frame(spec, 0, 0)
    fov = detect_fov(frame)
    for frame_id in range(10):
        tracker.update(frame_id, frame, fov)
    assert tracker.offset(9) == pytest.approx((0, 0), abs=0.5)


def test_registration_leaves_the_keyframe_untouched():
    # cv2.phaseCorrelate multiplies its window into its inputs in place when the patch
    # size is DFT-optimal (this eyepiece gives 200x200). The keyframe was then windowed
    # again on every frame until it was mostly window, biasing registration toward zero.
    spec = texture(5)
    tracker = MotionTracker()
    fov = detect_fov(eyepiece_frame(spec, 0, 0))
    tracker.update(0, eyepiece_frame(spec, 0, 0), fov)
    assert cv2.getOptimalDFTSize(tracker._window[2]) == tracker._window[2]
    key = tracker._key_patch.copy()
    for frame_id in range(1, 6):
        tracker.update(frame_id, eyepiece_frame(spec, 0, 0), fov)
    assert np.array_equal(tracker._key_patch, key)


# --- candidates -------------------------------------------------------------------------

def dotted_frame(dx: int, dy: int) -> np.ndarray:
    spec = np.full((2000, 2000), 150, np.uint8)
    spec = cv2.GaussianBlur((spec + np.random.default_rng(4).normal(0, 4, spec.shape)).clip(0, 255).astype(np.uint8), (0, 0), 1)
    cv2.circle(spec, (300 + 700, 300 + 300), 7, 40, -1)  # dark dot
    cv2.circle(spec, (300 + 500, 300 + 450), 10, 230, -1)  # bright blob
    return eyepiece_frame(spec, dx, dy)


def test_proposes_dark_and_bright_spots():
    frame = dotted_frame(0, 0)
    found = propose(frame, detect_fov(frame))
    dark = [c for c in found if c.contrast < 0]
    bright = [c for c in found if c.contrast > 0]
    assert any(abs(c.x - 700) < 6 and abs(c.y - 300) < 6 for c in dark)
    assert any(abs(c.x - 500) < 6 and abs(c.y - 450) < 6 for c in bright)


def test_candidate_ids_survive_stage_motion():
    tracker = CandidateTracker()
    frame0 = dotted_frame(0, 0)
    fov = detect_fov(frame0)
    first = {c.id: (c.x, c.y) for c in tracker.update(0, frame0, fov, (0, 0))}
    dot_id = next(i for i, (x, y) in first.items() if abs(x - 700) < 6 and abs(y - 300) < 6)
    moved = tracker.update(1, dotted_frame(30, -20), fov, (30, -20))
    same = next(c for c in moved if c.id == dot_id)
    assert (same.x, same.y) == pytest.approx((730, 280), abs=6)

