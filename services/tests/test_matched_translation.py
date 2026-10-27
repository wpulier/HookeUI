"""motion.matched_translation computes every candidate's inliers at once; it must agree exactly with the one-by-one
vote it replaced (the slide's move is what most of the view agrees on: MotionTracker._consensus)."""
import numpy as np

from orchestrator.motion import Features, matched_translation


def one_by_one(a, b, min_inliers=12, grid=4):
    """The vote as it was written before 2026-10-11 (each candidate's inliers in turn)."""
    import cv2

    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(a.descriptors, b.descriptors)
    if len(matches) < 15:
        return None
    pa = a.points[[m.queryIdx for m in matches]]
    pb = b.points[[m.trainIdx for m in matches]]
    d = pb - pa
    best, best_mask = None, None
    rng = np.random.default_rng(0)
    for i in rng.choice(len(d), size=min(200, len(d)), replace=False):
        inliers = np.linalg.norm(d - d[i], axis=1) < 2.0
        if best_mask is None or inliers.sum() > best_mask.sum():
            best_mask, best = inliers, d[inliers].mean(axis=0)
    if best_mask is None or int(best_mask.sum()) < min_inliers:
        return None
    x, y, w, h = b.box
    cells = {(min(grid - 1, int((px - x) / w * grid)), min(grid - 1, int((py - y) / h * grid)))
             for px, py in pb[best_mask] if x <= px < x + w and y <= py < y + h}
    return float(best[0]), float(best[1]), int(best_mask.sum()), len(cells) / (grid * grid)


def features(rng, n, shift=(0.0, 0.0), noise=0.0, outliers=0.0):
    descriptors = rng.integers(0, 256, (n, 32), dtype=np.uint8)
    points = rng.uniform(0, 400, (n, 2)).astype(np.float32)
    a = Features(points, descriptors, (0, 0, 400, 400))
    moved = points + np.float32(shift) + rng.normal(0, noise, (n, 2)).astype(np.float32)
    wild = rng.random(n) < outliers
    moved[wild] = rng.uniform(0, 400, (int(wild.sum()), 2))
    return a, Features(moved.astype(np.float32), descriptors.copy(), (0, 0, 400, 400))


def test_the_vote_agrees_exactly_with_the_one_by_one_vote():
    rng = np.random.default_rng(11)
    cases = [dict(n=500, shift=(12.5, -3.0), noise=0.4, outliers=0.3),  # a slide moved, some matches wrong
             dict(n=60, shift=(0.0, 0.0), noise=1.5, outliers=0.5),  # still, noisy
             dict(n=300, shift=(-40.0, 22.0), noise=0.0, outliers=0.8),  # mostly wrong: ties likely
             dict(n=16, shift=(3.0, 3.0), noise=3.0, outliers=0.0),  # few matches
             dict(n=10, shift=(1.0, 1.0))]  # too few to vote
    for case in cases:
        for _ in range(5):
            a, b = features(rng, **case)
            assert matched_translation(a, b) == one_by_one(a, b), case
