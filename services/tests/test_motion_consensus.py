"""The slide's move is what most of the view agrees on: things swimming on their own are outvoted
(motion.MotionTracker._consensus; bench/tracking.py measures it on eyepiece tours)."""
import cv2
import numpy as np

from orchestrator import motion as M
from orchestrator.motion import MotionTracker


def test_swimmers_do_not_drag_the_slide_offset():
    """A sparse, pale slide (small static specks) with dark cells carried across it by a current, as
    in a wet mount: phase correlation alone follows the cells (537 px off after 2 s; the consensus
    check keeps it within a few px)."""
    rng = np.random.default_rng(3)
    slide = np.full((900, 1600), 200, np.uint8)
    for _ in range(900):
        cv2.circle(slide, (int(rng.uniform(0, 1600)), int(rng.uniform(0, 900))), int(rng.uniform(1, 3)), 120, -1)
    slide = cv2.GaussianBlur(slide, (0, 0), 1.0)
    cells = [{"x": rng.uniform(0, 1280), "y": rng.uniform(0, 720)} for _ in range(30)]
    tracker = MotionTracker()
    first = None
    for i in range(60):
        x, y = 100 + 2 * i, 60 + i  # the slide moves: content 2 px left, 1 px up each frame
        view = slide[y:y + 720, x:x + 1280].copy()
        for c in cells:
            c["x"], c["y"] = (c["x"] + 7 + rng.normal(0, 0.5)) % 1280, (c["y"] + rng.normal(0, 0.5)) % 720
            cv2.ellipse(view, (int(c["x"]), int(c["y"])), (12, 7), 0, 0, 360, 40, -1)
        sample = tracker.update(i, view, None)
        first = first or sample
    assert sample.epoch == first.epoch
    assert np.hypot((sample.dx - first.dx) + 118, (sample.dy - first.dy) + 59) < 6


def test_matched_translation_ignores_a_minority_moving_on_its_own():
    rng = np.random.default_rng(5)
    a = cv2.GaussianBlur((rng.random((288, 512)) * 255).astype(np.uint8), (0, 0), 1.5)
    b = np.roll(a, (4, 9), (0, 1)).copy()
    b[100:160, 200:280] = np.roll(a[100:160, 200:280], (0, -20), (0, 1))  # a patch moving the other way
    window = (20, 20, 470, 250)
    found = M.matched_translation(M.orb_features(a, window), M.orb_features(b, window), min_inliers=20)
    assert found is not None and abs(found[0] - 9) < 1.5 and abs(found[1] - 4) < 1.5 and found[3] >= 0.25


def wet_mount(seed=3, frames=60, swimmers=30):
    """The swimmers' view of the first test, frame by frame (the slide moves 2 px left and 1 up a frame)."""
    rng = np.random.default_rng(seed)
    slide = np.full((900, 1600), 200, np.uint8)
    for _ in range(900):
        cv2.circle(slide, (int(rng.uniform(0, 1600)), int(rng.uniform(0, 900))), int(rng.uniform(1, 3)), 120, -1)
    slide = cv2.GaussianBlur(slide, (0, 0), 1.0)
    cells = [{"x": rng.uniform(0, 1280), "y": rng.uniform(0, 720)} for _ in range(swimmers)]
    for i in range(frames):
        x, y = 100 + 2 * i, 60 + i
        view = slide[y:y + 720, x:x + 1280].copy()
        for c in cells:
            c["x"], c["y"] = (c["x"] + 7 + rng.normal(0, 0.5)) % 1280, (c["y"] + rng.normal(0, 0.5)) % 720
            cv2.ellipse(view, (int(c["x"]), int(c["y"])), (12, 7), 0, 0, 360, 40, -1)
        yield i, view


def test_a_frames_features_are_computed_once_and_the_track_is_the_same(monkeypatch):
    """A frame that becomes the keyframe reuses the features its vote computed (before 2026-10-11 they were computed
    twice): the same samples as computing them afresh each time."""
    def run(fresh: bool):
        tracker = MotionTracker()
        if fresh:
            monkeypatch.setattr(tracker, "_features", lambda frame_id, small, window: M.orb_features(small, window))
        return [tracker.update(i, view, None) for i, view in wet_mount()]

    reused = run(False)
    assert reused == run(True)


def off_the_slide(samples) -> list[float]:
    return [float(np.hypot(s.dx + 2 * s.frame_id, s.dy + s.frame_id)) for s in samples]  # the truth: 2 px left, 1 up


def test_voting_every_few_frames_holds_on_still_tissue_and_loses_swimmers():
    """Option MOTION_ORB_EVERY (default 1). On tissue with nothing swimming, voting every third frame keeps the track
    and skips most of the ORB work; with swimmers, phase correlation stands between votes and is dragged, and once a
    vote finds too few matches the drag runs on (the reason the default votes every frame)."""
    still = MotionTracker()
    still.orb_every = 3
    samples = [still.update(i, view, None) for i, view in wet_mount(swimmers=0)]
    assert still.consensus["skipped"] > len(samples) / 2 and max(off_the_slide(samples)) < 6
    every, third = MotionTracker(), MotionTracker()
    third.orb_every = 3
    assert off_the_slide([every.update(i, v, None) for i, v in wet_mount()])[-1] < 6
    assert off_the_slide([third.update(i, v, None) for i, v in wet_mount()])[-1] > 20
