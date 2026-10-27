"""Track how the specimen slides across the camera frame.

Microscope motion is dominated by translation (moving the stage), so every frame gets a
cumulative content offset instead of running a per-object tracker. Content at pixel p in
frame F appears at p + (offset[N] - offset[F]) in frame N.

Registration uses phase correlation on a Hann-windowed square inside the eyepiece disc.
The black surround never enters the window: it is fixed to the camera, so including it
would pull every estimate toward zero shift. Frames register against a keyframe rather
than the previous frame, so small per-frame errors do not accumulate into drift.

Blur and refocusing produce spurious correlation peaks that look like large jumps. Every
frame is therefore registered twice, against the keyframe and against the previous frame,
and a keyframe estimate is only accepted when the two agree. When they disagree, a small
plausible frame-to-frame step is used instead; otherwise the frame is marked untracked.
A keyframe estimate with no frame-to-frame check behind it (right after untracked frames)
must clear a higher bar. If tracking stays lost (typically a fast, blurred pan), ORB
feature matching against the last well-tracked frame tries to bridge the gap. Only if that
also fails does a new keyframe start a new `epoch`: the offset stays numerically continuous
but is not trustworthy across the gap, so anything anchored in an older epoch has to be
re-found in the image.

Fast pans are tracked frame-to-frame through blur, so small errors add up during the pan.
When the view settles again, the settled frame is matched directly (ORB, full resolution)
against the last steady frame before the pan, and the offset is corrected ("loop
closure"). Overlays snap back onto the specimen instead of staying off by the
accumulated error.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

import cv2
import numpy as np

from .fov import FieldOfView


# The consensus check (MotionTracker._consensus): feature matches against the keyframe vote on
# the slide's move; phase correlation is overruled when the vote disagrees by more than this.
CONSENSUS_PX = 2.0  # analysis px (about 5 full-resolution px); tighter measured worse (bench/tracking.py)
CONSENSUS_INLIERS = 20  # agreeing matches needed to vote
CONSENSUS_SPREAD = 0.25  # ...spread over at least this share of a 4x4 grid of the view

BANK_CELL_PX = 200.0  # one remembered still view per this much slide movement (full-res px)
BANK_SIZE = 120  # remembered still views (the oldest go first)
REJOIN_EVERY = 5  # frames between rejoin checks after continuity was lost
REJOIN_FRAMES = 30 * 30  # ...for at most this many frames (30 s at 30 fps)
REJOIN_TRIES = 6  # remembered views compared per check, newest first
REJOIN_INLIERS = 30  # agreeing ORB matches a rejoin needs
REJOIN_FEATURES = 800  # ORB features per remembered view (more than the per-frame consensus check's 500)
REJOIN_SPREAD = 0.4  # ...spread over at least this share of a 4x4 grid of the view


@dataclass(frozen=True)
class MotionSample:
    frame_id: int
    dx: float  # cumulative content offset in full-resolution pixels
    dy: float
    confidence: float  # phase-correlation peak response of the registration used
    tracked: bool  # False when registration failed and the offset was carried over
    keyframe_id: int
    epoch: int = 0  # increments when tracking was lost long enough to break continuity
    steady: bool = False  # view has been still for a few frames and any pan was loop-closed


class MotionTracker:
    def __init__(
        self,
        analysis_width: int = 512,
        min_response: float = 0.06,
        refresh_response: float = 0.18,
        refresh_shift_fraction: float = 0.18,
        max_step_fraction: float = 0.08,
        agreement_px: float = 2.0,
        max_lost_frames: int = 5,
        steady_px_per_frame: float = 1.5,
        pan_px_per_frame: float = 6.0,
        settle_frames: int = 5,
        history: int = 300,
    ) -> None:
        self.analysis_width = analysis_width
        self.min_response = min_response
        self.refresh_response = refresh_response
        self.refresh_shift_fraction = refresh_shift_fraction
        self.max_step_fraction = max_step_fraction
        self.agreement_px = agreement_px
        self.max_lost_frames = max_lost_frames
        self.steady_px_per_frame = steady_px_per_frame
        self.pan_px_per_frame = pan_px_per_frame
        self.settle_frames = settle_frames
        self.history = history
        self.corrections: list[dict] = []  # loop-closure corrections applied (for diagnostics)
        self._steady_count = 0
        self._steady_ref: tuple[np.ndarray, float, float] | None = None  # full-res gray, dx, dy
        self._panned = False
        self.epoch = 0
        # The highest epoch number given so far: a new epoch always takes the next one. A rejoin goes back to
        # an earlier number, so "the current one plus one" can be a number already used.
        self._newest_epoch = 0
        self._lost = 0
        self.samples: OrderedDict[int, MotionSample] = OrderedDict()
        self._window: tuple[int, int, int, int] | None = None  # in analysis pixels
        self._hann: np.ndarray | None = None
        self._scale = 1.0
        self._key_patch: np.ndarray | None = None
        self._key_id = -1
        self._key_offset = (0.0, 0.0)
        self._prev_patch: np.ndarray | None = None
        self._prev_rel: tuple[float, float] | None = None  # prev frame vs keyframe, analysis px
        self._last_good: MotionSample | None = None
        self._last_good_small: np.ndarray | None = None
        self._key_features: Features | None = None  # ORB features of the keyframe (the consensus check)
        self.consensus = {"checked": 0, "overruled": 0, "no_vote": 0, "skipped": 0}  # for diagnostics and benches
        # The consensus vote every this many frames (option MOTION_ORB_EVERY, set by the hub; 1: every frame). A frame
        # phase correlation could not place is always voted on. ORB is most of the frame path's time (2026-10-11).
        self.orb_every = 1
        self._since_vote = 0
        self._this_features: tuple[np.ndarray, Features | None] | None = None  # (this frame's small view, its ORB features)
        # Places seen before (W5): one still view per area of the slide, per epoch, so a view
        # that comes back after continuity was lost rejoins its old epoch (and its outlines).
        self.bank: OrderedDict[tuple[int, int, int], tuple[Features, float, float]] = OrderedDict()
        self.rejoins: list[dict] = []  # for diagnostics
        self._refind_until = 0.0  # frames after an epoch change during which a rejoin is tried
        self._refind_candidate: tuple | None = None  # (bank key, shift) waiting for confirmation
        self._frames = 0

    def reset(self) -> None:
        self.samples.clear()
        self._key_patch = None
        self._window = None
        self._last_good = None

    def update(self, frame_id: int, gray: np.ndarray, fov: FieldOfView | None) -> MotionSample:
        self._this_features = None  # this frame's ORB features, once computed (_features)
        height, width = gray.shape[:2]
        scale = self.analysis_width / width
        small = cv2.resize(gray, (self.analysis_width, int(round(height * scale))), interpolation=cv2.INTER_AREA)
        window = self._analysis_window(small.shape[1], small.shape[0], fov, scale)
        if window != self._window or self._key_patch is None or scale != self._scale:
            if self._key_patch is not None and scale != self._scale:
                # Another picture size (a camera or a recording of another size): offsets in its pixels are not the
                # old ones, and nothing relates the two views, so continuity is not assumed (the review of #178).
                return self._new_epoch(frame_id, small, window, scale)
            if self._key_patch is not None and self._last_good is not None:
                # The eyepiece circle's fit moved (the lens refocused, the phone shifted) or the picture cut to another
                # view: whether it is the same view is checked, never assumed (2026-10-07, flow 6 on footage: a cut kept
                # epoch 0 and the outlines stayed drawn over another view).
                moved = self._across(small, window)
                if moved is None:
                    return self._new_epoch(frame_id, small, window, scale)
                last = self._last_good
                self._last_good = MotionSample(frame_id, last.dx + moved[0] / scale, last.dy + moved[1] / scale,
                                               last.confidence, True, last.keyframe_id, self.epoch)
            return self._start_keyframe(frame_id, small, window, scale)

        patch = self._patch(small)
        (kx, ky), key_response = phase_correlate(self._key_patch, patch, self._hann)
        step = None
        if self._prev_patch is not None and self._prev_rel is not None:
            (px, py), step_response = phase_correlate(self._prev_patch, patch, self._hann)
            if step_response >= self.min_response and np.hypot(px, py) <= self.max_step_fraction * window[2]:
                step = (self._prev_rel[0] + px, self._prev_rel[1] + py, step_response)
        self._prev_patch = patch

        if step is not None:
            agrees = key_response >= self.min_response and np.hypot(kx - step[0], ky - step[1]) <= self.agreement_px
            rel, response = ((kx, ky), key_response) if agrees else ((step[0], step[1]), step[2])
        elif key_response >= self.refresh_response:
            rel, response = (kx, ky), key_response
        else:
            rel, response = None, key_response
        # The consensus check: phase correlation follows whatever moves most in the view, so cells
        # swimming over a still slide drag it along (2026-10-06: outlines drawn 100s of px off). Many
        # independent feature matches against the keyframe vote; things that move on their own are
        # outvoted, and the slide's own move wins. Too few features to vote: phase correlation stands.
        self._since_vote += 1
        voted = None
        # A vote is skipped only where phase correlation alone would keep the keyframe: a keyframe taken on an
        # unvoted frame would carry a swimmer's drag into every later offset (a wet mount, every third frame voted:
        # 45 px off after 2 s), while an unvoted frame against a voted keyframe is corrected by the next vote.
        keeps_key = (rel is not None and rel == (kx, ky) and key_response >= self.refresh_response
                     and np.hypot(*rel) <= self.refresh_shift_fraction * window[2])
        if not keeps_key or self._since_vote >= max(1, int(self.orb_every)):
            self._since_vote = 0
            voted = self._consensus(small, window, frame_id)
        else:
            self.consensus["skipped"] += 1
        if voted is not None and (rel is None or np.hypot(rel[0] - voted[0], rel[1] - voted[1]) > CONSENSUS_PX):
            if rel is not None:
                self.consensus["overruled"] += 1
            rel, response = (voted[0], voted[1]), max(float(response), self.min_response)
        if rel is None:
            return self._lost_frame(frame_id, small, window, scale, float(key_response))

        self._lost = 0
        dx = self._key_offset[0] + rel[0] / scale
        dy = self._key_offset[1] + rel[1] / scale
        previous = self._last_good
        sample = MotionSample(frame_id, dx, dy, float(response), True, self._key_id, self.epoch)
        self._store(sample)
        self._last_good = sample
        self._last_good_small = small
        self._prev_rel = rel

        speed = float(np.hypot(dx - previous.dx, dy - previous.dy)) if previous else 0.0
        correction = self._loop_closure(gray, fov, sample, speed)
        steady = self._steady_count >= self.settle_frames and not self._panned
        if correction is not None:
            cx, cy = correction
            dx, dy = dx + cx, dy + cy
            self._key_offset = (self._key_offset[0] + cx, self._key_offset[1] + cy)
        if correction is not None or steady:
            sample = MotionSample(frame_id, dx, dy, sample.confidence, True, self._key_id, self.epoch, steady)
            self._store(sample)
            self._last_good = sample

        self._frames += 1
        if steady and self._refind_until > self._frames and self._frames % REJOIN_EVERY == 0:
            rejoined = self._try_rejoin(frame_id, small, window, scale, (dx, dy))
            if rejoined is not None:
                return rejoined
        moved_far = np.hypot(*rel) > self.refresh_shift_fraction * window[2]
        if moved_far or key_response < self.refresh_response or rel != (kx, ky):
            self._key_patch = patch
            self._key_id = frame_id
            self._key_offset = (dx, dy)
            self._prev_rel = (0.0, 0.0)
            self._key_features = self._features(frame_id, small, window)
        return sample

    def _features(self, frame_id: int, small: np.ndarray, window) -> Features | None:
        """This frame's ORB features, computed once: the vote's, reused when the frame becomes the keyframe (the
        same features: before 2026-10-11 they were computed twice on such a frame)."""
        if self._this_features is not None and self._this_features[0] is small:
            return self._this_features[1]
        features = orb_features(small, window)
        self._this_features = (small, features)
        return features

    def _consensus(self, small: np.ndarray, window, frame_id: int | None = None) -> tuple[float, float] | None:
        """The keyframe-to-now move most feature matches agree on (analysis px), or None when
        too few agree to vote."""
        if self._key_features is None:
            return None
        self.consensus["checked"] += 1
        here = self._features(frame_id, small, window) if frame_id is not None else orb_features(small, window)
        found = matched_translation(self._key_features, here, min_inliers=CONSENSUS_INLIERS)
        if found is None or found[3] < CONSENSUS_SPREAD:
            self.consensus["no_vote"] += 1
            return None
        return found[0], found[1]

    def _loop_closure(
        self, gray: np.ndarray, fov: FieldOfView | None, sample: MotionSample, speed: float
    ) -> tuple[float, float] | None:
        if speed > self.pan_px_per_frame:
            self._panned = True
            self._steady_count = 0
            return None
        if speed > self.steady_px_per_frame:
            self._steady_count = 0
            return None
        self._steady_count += 1
        if self._steady_count < self.settle_frames:
            return None
        correction = None
        if self._panned and self._steady_ref is not None:
            ref_gray, ref_dx, ref_dy = self._steady_ref
            mask = None
            if fov is not None:
                mask = fov.mask(gray.shape[1], gray.shape[0], erode_px=int(fov.radius * 0.08))
            found = orb_translation(ref_gray, gray, mask, features=2500, min_inliers=25)
            if found is not None:
                expected = (sample.dx - ref_dx, sample.dy - ref_dy)
                cx, cy = found[0] - expected[0], found[1] - expected[1]
                if 3.0 < np.hypot(cx, cy) < 0.4 * gray.shape[1]:
                    correction = (cx, cy)
                    self.corrections.append({"frame": sample.frame_id, "dx": round(cx, 1), "dy": round(cy, 1), "inliers": found[2]})
        self._panned = False
        # Refresh the steady reference while the view is still, so it stays recent.
        if self._steady_count == self.settle_frames or self._steady_count % 30 == 0:
            offset = (sample.dx + (correction or (0, 0))[0], sample.dy + (correction or (0, 0))[1])
            self._steady_ref = (gray, offset[0], offset[1])
            self._remember(offset)
        return correction

    def _remember(self, offset: tuple[float, float]) -> None:
        """Keep this still view in the bank (one per area of the slide, per epoch)."""
        if self._last_good_small is None or self._window is None:
            return
        features = orb_features(self._last_good_small, self._window, features=REJOIN_FEATURES)
        if features is None:
            return
        key = (self.epoch, int(round(offset[0] / BANK_CELL_PX)), int(round(offset[1] / BANK_CELL_PX)))
        self.bank.pop(key, None)
        self.bank[key] = (features, offset[0], offset[1])
        while len(self.bank) > BANK_SIZE:
            self.bank.popitem(last=False)

    def _try_rejoin(self, frame_id: int, small: np.ndarray, window, scale: float,
                    offset: tuple[float, float]) -> MotionSample | None:
        """After continuity was lost, is this a place seen in an earlier epoch? A match against
        the bank (ORB, enough agreeing matches spread over the view) seen on two checks in a row
        rejoins that epoch: the offset continues from there, so outlines made then are placed
        again (never on an unverified match). Returns the rejoined sample, or None."""
        here = orb_features(small, window, features=REJOIN_FEATURES)  # once per check; the bank keeps its own (about 1 ms a match)
        if here is None:
            return None
        tried = 0
        for key in reversed(list(self.bank)):
            if key[0] == self.epoch or tried >= REJOIN_TRIES:
                continue
            tried += 1
            ref, ref_dx, ref_dy = self.bank[key]
            found = matched_translation(ref, here, min_inliers=REJOIN_INLIERS)
            if found is None or found[3] < REJOIN_SPREAD:
                continue
            shift = (found[0] / scale, found[1] / scale)
            candidate = self._refind_candidate
            self._refind_candidate = (key, shift)
            if candidate is None or candidate[0] != key or np.hypot(candidate[1][0] - shift[0], candidate[1][1] - shift[1]) > 3.0 / scale:
                return None  # seen once: confirm on the next check
            old_epoch = key[0]
            dx, dy = ref_dx + shift[0], ref_dy + shift[1]
            # The rejoin is a verified translation between the two epochs at this frame: a place in the
            # epoch left is at itself + delta in the one rejoined (the hub carries outlines made since
            # the loss over by it; they are then checked by their own pixels).
            self.rejoins.append({"frame": frame_id, "from": self.epoch, "to": old_epoch, "inliers": found[2],
                                 "delta": (float(dx - offset[0]), float(dy - offset[1]))})
            self.epoch = old_epoch
            self._refind_until, self._refind_candidate, self._steady_ref = 0, None, None
            self._last_good = MotionSample(frame_id, dx, dy, 1.0, True, frame_id, self.epoch)
            return self._start_keyframe(frame_id, small, window, scale)
        return None

    def _lost_frame(
        self, frame_id: int, small: np.ndarray, window: tuple[int, int, int, int], scale: float, response: float
    ) -> MotionSample:
        self._prev_rel = None
        self._lost += 1
        if self._lost >= self.max_lost_frames:
            self._lost = 0
            bridged = self._bridge(small, window)
            if bridged is None:
                return self._new_epoch(frame_id, small, window, scale)
            last = self._last_good
            self._last_good = MotionSample(
                frame_id, last.dx + bridged[0] / scale, last.dy + bridged[1] / scale, 0.0, True, frame_id, self.epoch
            )
            return self._start_keyframe(frame_id, small, window, scale)
        carried = self._last_good or MotionSample(frame_id, 0.0, 0.0, 0.0, False, self._key_id, self.epoch)
        sample = MotionSample(frame_id, carried.dx, carried.dy, response, False, self._key_id, self.epoch)
        self._store(sample)
        return sample

    def _across(self, small: np.ndarray, window: tuple[int, int, int, int]) -> tuple[float, float] | None:
        """The move from the last well-tracked frame to this one when the analysis window changed (analysis px), or
        None when it is not the same view: phase correlation in the new window at the higher bar (a keyframe estimate
        with no frame-to-frame check behind it), else feature matches (the bridge)."""
        before = self._last_good_small
        if before is None or before.shape != small.shape:
            return None
        x, y, w, h = window
        hann = cv2.createHanningWindow((w, h), cv2.CV_32F)

        def patch(img):
            p = img[y : y + h, x : x + w].astype(np.float32)
            return p - cv2.GaussianBlur(p, (0, 0), 8)

        (px, py), response = phase_correlate(patch(before), patch(small), hann)
        if response >= self.refresh_response and np.hypot(px, py) <= self.max_step_fraction * w:
            return float(px), float(py)
        return self._bridge(small, window)

    def _new_epoch(self, frame_id: int, small: np.ndarray, window: tuple[int, int, int, int], scale: float) -> MotionSample:
        """Continuity lost: a new epoch from this frame; what was anchored before has to be re-found in the image, and a
        place seen before can be rejoined for a while. Its number was never used before in this run: offsets are
        compared, rejoins linked (hub.epoch_links), places remembered (`bank`) and slide maps kept by it. After a
        rejoin back to epoch 0, the next loss took epoch 1 again, so the link, the remembered views and the map of
        the first epoch 1 were taken for the new one's."""
        self._newest_epoch += 1
        self.epoch = self._newest_epoch
        self._lost = 0
        self._steady_ref = None
        self._refind_until = self._frames + REJOIN_FRAMES  # try to rejoin a place seen before
        self._refind_candidate = None
        return self._start_keyframe(frame_id, small, window, scale)

    def _bridge(self, small: np.ndarray, window: tuple[int, int, int, int]) -> tuple[float, float] | None:
        if self._last_good is None or self._last_good_small is None:
            return None
        x, y, w, h = window
        mask = np.zeros(small.shape[:2], np.uint8)
        mask[y : y + h, x : x + w] = 255
        found = orb_translation(self._last_good_small, small, mask, features=1500, min_inliers=15)
        return None if found is None else (found[0], found[1])

    def same_epoch(self, frame_a: int, frame_b: int) -> bool:
        a, b = self.samples.get(frame_a), self.samples.get(frame_b)
        return bool(a and b and a.epoch == b.epoch)

    def offset(self, frame_id: int) -> tuple[float, float] | None:
        sample = self.samples.get(frame_id)
        return (sample.dx, sample.dy) if sample else None

    def map_point(self, x: float, y: float, from_frame: int, to_frame: int) -> tuple[float, float] | None:
        a, b = self.offset(from_frame), self.offset(to_frame)
        if a is None or b is None:
            return None
        return x + b[0] - a[0], y + b[1] - a[1]

    def _start_keyframe(
        self, frame_id: int, small: np.ndarray, window: tuple[int, int, int, int], scale: float
    ) -> MotionSample:
        # Window or scale changed (eyepiece moved, new stream): keep the cumulative offset
        # continuous and register future frames against this one.
        self._window = window
        self._scale = scale
        self._hann = cv2.createHanningWindow((window[2], window[3]), cv2.CV_32F)
        self._key_patch = self._patch(small)
        self._prev_patch = self._key_patch
        self._prev_rel = (0.0, 0.0)
        self._key_id = frame_id
        prev = self._last_good
        self._key_offset = (prev.dx, prev.dy) if prev else (0.0, 0.0)
        self._last_good_small = small
        self._key_features = self._features(frame_id, small, window)
        sample = MotionSample(frame_id, self._key_offset[0], self._key_offset[1], 1.0, True, frame_id, self.epoch)
        self._store(sample)
        self._last_good = sample
        return sample

    def _patch(self, small: np.ndarray) -> np.ndarray:
        x, y, w, h = self._window
        patch = small[y : y + h, x : x + w].astype(np.float32)
        # Remove slow illumination gradients so the correlation locks onto structure.
        patch -= cv2.GaussianBlur(patch, (0, 0), 8)
        return patch

    def _analysis_window(
        self, width: int, height: int, fov: FieldOfView | None, scale: float
    ) -> tuple[int, int, int, int]:
        if fov is None:
            margin_x, margin_y = int(width * 0.1), int(height * 0.1)
            return margin_x, margin_y, width - 2 * margin_x, height - 2 * margin_y
        x, y, w, h = fov.inscribed_square()
        x0 = max(0, int(x * scale))
        y0 = max(0, int(y * scale))
        x1 = min(width, int((x + w) * scale))
        y1 = min(height, int((y + h) * scale))
        # Quantize so tiny frame-to-frame changes in the fitted circle don't reset tracking.
        q = 8
        x0, y0 = (x0 + q - 1) // q * q, (y0 + q - 1) // q * q
        x1, y1 = x1 // q * q, y1 // q * q
        return x0, y0, max(q, x1 - x0), max(q, y1 - y0)

    def _store(self, sample: MotionSample) -> None:
        self.samples[sample.frame_id] = sample
        while len(self.samples) > self.history:
            self.samples.popitem(last=False)


def phase_correlate(a: np.ndarray, b: np.ndarray, window: np.ndarray) -> tuple[tuple[float, float], float]:
    """cv2.phaseCorrelate on copies. When the patch size is already DFT-optimal (192, 200,
    256... which the eyepiece window usually is), OpenCV multiplies the window into its
    inputs in place, so the stored keyframe and previous patches were windowed again on
    every frame. That biased registration toward zero shift and cost accuracy
    (bench.motion_drift on the Jun 14 recording: error vs ORB p50 0.27 -> 0.08 px, while
    moving 2.31 -> 1.57 px, one spurious continuity break -> none)."""
    return cv2.phaseCorrelate(a.copy(), b.copy(), window)


def orb_translation(
    a: np.ndarray, b: np.ndarray, mask: np.ndarray | None, features: int = 3000, min_inliers: int = 12
) -> tuple[float, float, int] | None:
    """Pure-translation estimate from ORB matches with a simple consensus vote.
    Returns (dx, dy, inliers): content moved by (dx, dy) from a to b."""
    orb = cv2.ORB_create(features)
    ka, da = orb.detectAndCompute(a, mask)
    kb, db = orb.detectAndCompute(b, mask)
    if da is None or db is None or len(ka) < 20 or len(kb) < 20:
        return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
    if len(matches) < 15:
        return None
    pa = np.float32([ka[m.queryIdx].pt for m in matches])
    pb = np.float32([kb[m.trainIdx].pt for m in matches])
    d = pb - pa
    best, best_inliers = None, 0
    rng = np.random.default_rng(0)
    for i in rng.choice(len(d), size=min(300, len(d)), replace=False):
        inliers = np.linalg.norm(d - d[i], axis=1) < 3.0
        if inliers.sum() > best_inliers:
            best_inliers, best = int(inliers.sum()), d[inliers].mean(axis=0)
    if best_inliers < min_inliers:
        return None
    return float(best[0]), float(best[1]), best_inliers


@dataclass(frozen=True)
class Features:
    """ORB keypoints of one view, computed once so it can be compared with many others."""
    points: np.ndarray  # (n, 2) float32, analysis px
    descriptors: np.ndarray
    box: tuple[int, int, int, int]  # x, y, w, h: the part of the view they come from


def orb_features(small: np.ndarray, window: tuple[int, int, int, int], features: int = 500) -> Features | None:
    x, y, w, h = window
    mask = np.zeros(small.shape[:2], np.uint8)
    mask[y : y + h, x : x + w] = 255
    keypoints, descriptors = cv2.ORB_create(features).detectAndCompute(small, mask)
    if descriptors is None or len(keypoints) < 20:
        return None
    return Features(np.float32([k.pt for k in keypoints]), descriptors, (x, y, w, h))


def matched_translation(a: Features | None, b: Features | None, min_inliers: int = 12, grid: int = 4
                        ) -> tuple[float, float, int, float] | None:
    """Like orb_translation, on features computed beforehand. Returns (dx, dy, inliers, spread):
    content moved by (dx, dy) from a to b, and how evenly the agreeing matches cover b's view (the
    share of a 4x4 grid's cells holding one): matches bunched in one corner score low."""
    if a is None or b is None:
        return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(a.descriptors, b.descriptors)
    if len(matches) < 15:
        return None
    pa = a.points[[m.queryIdx for m in matches]]
    pb = b.points[[m.trainIdx for m in matches]]
    d = pb - pa
    rng = np.random.default_rng(0)
    candidates = rng.choice(len(d), size=min(200, len(d)), replace=False)
    # Every candidate's inliers at once (the frame path's costliest Python: 200 norms a frame took 2 ms of its
    # 10, live 2026-10-11): the same distances, and the first candidate with the most inliers, as one by one.
    inliers = np.linalg.norm(d[None, :, :] - d[candidates][:, None, :], axis=2) < 2.0
    best_mask = inliers[int(np.argmax(inliers.sum(axis=1)))]
    best = d[best_mask].mean(axis=0)
    if int(best_mask.sum()) < min_inliers:
        return None
    x, y, w, h = b.box
    cells = {(min(grid - 1, int((px - x) / w * grid)), min(grid - 1, int((py - y) / h * grid)))
             for px, py in pb[best_mask] if x <= px < x + w and y <= py < y + h}
    return float(best[0]), float(best[1]), int(best_mask.sum()), len(cells) / (grid * grid)
