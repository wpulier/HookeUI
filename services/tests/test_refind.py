"""Re-finding after continuity is lost (orchestrator/motion.py, roadmap W5): a still view is
remembered per area of the slide; when the view comes back after a jump, a verified match
rejoins the old epoch, with offsets continuing from it, so its outlines are placed again."""
import cv2
import numpy as np

from orchestrator import motion as M
from orchestrator.motion import MotionTracker


def slide(seed=5, size=(3000, 5000)):
    rng = np.random.default_rng(seed)
    texture = rng.integers(0, 255, size).astype(np.uint8)
    return cv2.GaussianBlur(texture, (0, 0), 3)


def view(big, x, y):
    return big[y : y + 720, x : x + 1280].copy()


def run(tracker, big, places, start_id=0):
    samples, fid = [], start_id
    for x, y, frames in places:
        for _ in range(frames):
            samples.append(tracker.update(fid, view(big, x, y), None))
            fid += 1
    return samples


def test_a_place_seen_before_rejoins_its_epoch():
    big = slide()
    tracker = MotionTracker()
    first = run(tracker, big, [(400, 300, 40)])  # still at A: remembered
    assert first[-1].steady and first[-1].epoch == 0 and tracker.bank
    away = run(tracker, big, [(3400, 1900, 60)], start_id=40)  # a jump far away: continuity lost
    assert away[-1].epoch >= 1
    back = run(tracker, big, [(430, 320, 80)], start_id=100)  # back near A (moved 30 px right, 20 down)
    assert back[-1].epoch == 0, tracker.rejoins  # rejoined A's epoch
    assert abs(back[-1].dx - (first[-1].dx - 30)) < 3 and abs(back[-1].dy - (first[-1].dy - 20)) < 3
    assert tracker.rejoins and tracker.rejoins[-1]["to"] == 0


def test_an_unrelated_place_is_never_rejoined():
    big, other = slide(5), slide(9)
    tracker = MotionTracker()
    run(tracker, big, [(400, 300, 40)])
    samples = []
    fid = 40
    for _ in range(120):  # somewhere never seen (another slide): it stays a new epoch
        samples.append(tracker.update(fid, view(other, 900, 900), None))
        fid += 1
    assert samples[-1].epoch >= 1 and not tracker.rejoins


def test_a_new_epoch_after_a_rejoin_never_takes_a_number_already_used():
    """Epoch numbers name the slide's coordinates for the run: rejoins are linked (hub.epoch_links), places
    remembered (the bank) and slide maps kept by them. After a rejoin back to epoch 0, the next loss of continuity
    took epoch 1 again (the current number plus one), so the first epoch 1's link, remembered views and map were
    taken for another place's."""
    big = slide()
    tracker = MotionTracker()
    seen = run(tracker, big, [(400, 300, 40)])  # A: epoch 0, remembered
    seen += run(tracker, big, [(3400, 1900, 60)], start_id=40)  # B: continuity lost (epoch 1)
    seen += run(tracker, big, [(430, 320, 80)], start_id=100)  # back at A: a new epoch, then rejoins epoch 0
    used = {s.epoch for s in seen}
    assert seen[-1].epoch == 0 and used == {0, 1, 2}, tracker.rejoins
    elsewhere = run(tracker, big, [(2000, 1500, 60)], start_id=180)  # C, never seen: continuity lost again
    assert elsewhere[-1].epoch == 3, sorted({s.epoch for s in elsewhere})  # a new number: never B's 1 again


def test_outlines_of_a_rejoined_epoch_are_placed_again_only_where_their_pixels_match():
    """Hub side (contract 3, ADR 0010): an outline lost when continuity broke goes back to tracking
    once motion rejoins its epoch only if its own pixels match where the slide's move puts it; the
    epoch coming back is not enough. An outline with nothing to check its place with stays as it is
    (not drawn), and one from another epoch is not placed."""
    from types import SimpleNamespace

    import cv2

    from orchestrator.hub import Hub
    from orchestrator.scene import SceneObject, capture_template

    rng = np.random.default_rng(3)
    gray = cv2.GaussianBlur(rng.integers(0, 255, (720, 1280), dtype=np.uint8), (0, 0), 2.0)
    before = np.roll(gray, -5, axis=1)  # the view when it was outlined: the slide 5 px to the left

    def outline(oid, epoch, template=True, wrong=False):
        obj = SceneObject(id=oid, kind="highlight", polygons=[[[100, 100], [160, 100], [160, 160], [100, 160]]],
                          anchor_frame=1, anchor_epoch=epoch, anchor_offset=(0.0, 0.0), source="ai", track="lost",
                          lost_since=1.0)
        if template:
            other = cv2.GaussianBlur(np.random.default_rng(9).integers(0, 255, (720, 1280), dtype=np.uint8), (0, 0), 2.0)
            source = other if wrong else before  # wrong: pixels that are nowhere in this view
            obj.template, obj.template_origin = capture_template(source, obj.bbox)
        return obj

    info = SimpleNamespace(id=50, dx=5.0, dy=0.0, epoch=0, fov=None)
    objects = [outline("o1", 0), outline("o2", 1, wrong=True), outline("o3", 0, template=False),
               outline("o4", 0, wrong=True), outline("o5", 1)]
    changes = dict(Hub._reanchor(None, objects, gray, info))
    assert changes["o1"]["track"] == "tracking"  # its pixels are where the slide's move puts it
    assert "o3" not in changes  # nothing to check its place with: left unverified
    assert changes.get("o4", {}).get("track") != "tracking"  # its pixels are not there
    assert changes.get("o2", {}).get("track") != "tracking"  # another epoch, and its pixels are not found
    assert changes["o5"]["track"] == "tracking"  # another epoch, re-found by its own pixels


def test_a_cut_to_another_view_with_another_eyepiece_fit_is_a_new_epoch():
    """2026-10-07, flow 6 on footage: a seek to another place, where the eyepiece circle fitted differently, kept
    epoch 0 with no move, so the outlines stayed drawn over another view. A change of the analysis window is now
    checked against the last well-tracked frame, never assumed to be the same view."""
    from orchestrator.fov import FieldOfView

    big, other = slide(5), slide(9)
    tracker = MotionTracker()
    a, b = FieldOfView(640.0, 360.0, 330.0, 1.0), FieldOfView(640.0, 360.0, 300.0, 1.0)
    fid = 0
    for _ in range(30):
        last = tracker.update(fid, view(big, 400, 300), a)
        fid += 1
    assert last.epoch == 0
    cut = tracker.update(fid, view(other, 900, 900), b)  # another place, another fit: one frame is enough
    assert cut.epoch == 1


def test_the_same_view_with_the_eyepiece_fit_changed_keeps_its_epoch_and_place():
    """Focus breathing (the phone's lens) or the fit moving: the same view stays the same epoch, its offset follows the
    slide's move across the change."""
    from orchestrator.fov import FieldOfView

    big = slide(5)
    tracker = MotionTracker()
    a, b = FieldOfView(640.0, 360.0, 330.0, 1.0), FieldOfView(640.0, 360.0, 300.0, 1.0)
    fid = 0
    for _ in range(30):
        before = tracker.update(fid, view(big, 400, 300), a)
        fid += 1
    after = tracker.update(fid, view(big, 410, 304), b)  # moved 10 px left and 4 up as the fit changed
    assert after.epoch == 0 and after.tracked
    assert abs(after.dx - (before.dx - 10)) < 2.5 and abs(after.dy - (before.dy - 4)) < 2.5


def test_a_slow_pan_across_a_change_of_the_eyepiece_fit_keeps_its_move():
    """The review of #178: across a window change in a pan, the offset follows the move (14 px off before)."""
    from orchestrator.fov import FieldOfView

    big = slide(5)
    tracker = MotionTracker()
    a, b = FieldOfView(640.0, 360.0, 330.0, 1.0), FieldOfView(640.0, 360.0, 300.0, 1.0)
    samples = []
    for i in range(40):
        samples.append(tracker.update(i, view(big, 400 + 3 * i, 300 + i), a if i < 20 else b))
    first, last = samples[0], samples[-1]
    assert last.epoch == 0 and abs((first.dx - last.dx) - 3 * 39) < 3 and abs((first.dy - last.dy) - 39) < 3


def test_across_a_window_change_the_bar_the_cap_and_the_bridge_decide(monkeypatch):
    """The review of #178: what decides continuity across a window change. Phase correlation counts only at the
    keyframe bar and within the step cap; failing that the feature bridge decides; failing both, it is not the same view."""
    big = slide(5)
    tracker = MotionTracker()
    for i in range(10):
        tracker.update(i, view(big, 400, 300), None)
    small = tracker._last_good_small
    window = (16, 16, 160, 96)
    monkeypatch.setattr(M, "phase_correlate", lambda *a, **k: ((1.0, 1.0), tracker.refresh_response - 0.01))
    monkeypatch.setattr(tracker, "_bridge", lambda *a, **k: None)
    assert tracker._across(small, window) is None  # under the bar, no bridge: not the same view
    monkeypatch.setattr(M, "phase_correlate", lambda *a, **k: ((window[2], 0.0), 0.9))
    assert tracker._across(small, window) is None  # a step past the cap is not believed
    monkeypatch.setattr(tracker, "_bridge", lambda *a, **k: (2.0, -1.0))
    assert tracker._across(small, window) == (2.0, -1.0)  # the bridge decides when phase correlation can't
    monkeypatch.setattr(M, "phase_correlate", lambda *a, **k: ((1.5, 0.5), 0.9))
    assert tracker._across(small, window) == (1.5, 0.5)


def test_another_picture_size_is_a_new_epoch():
    """The review of #178: a feed of another size kept the epoch and the offset unchecked."""
    big = slide(5)
    tracker = MotionTracker()
    for i in range(10):
        last = tracker.update(i, view(big, 400, 300), None)
    other = cv2.resize(view(big, 400, 300), (960, 540))
    after = tracker.update(10, other, None)
    assert last.epoch == 0 and after.epoch == 1
