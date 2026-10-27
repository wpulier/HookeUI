"""A clearer picture from real frames (orchestrator/enhance.py), on synthetic frames whose truth is
known: averaging lowers the noise, a focus stack takes each place from the frame where it is
sharp, and both align frames by the slide offset (a point p of a frame is at
p + (offset_newest - offset_frame) in the newest one, as the studio draws outlines)."""
import cv2
import numpy as np
import pytest

from orchestrator import enhance

M = 40  # margin of the synthetic slide around the camera window


def slide(h=200, w=260, seed=1) -> np.ndarray:
    """A textured specimen bigger than the camera window, so frames can be cut from it shifted."""
    rng = np.random.default_rng(seed)
    base = rng.uniform(0, 255, (h + 2 * M, w + 2 * M)).astype(np.float32)
    return cv2.GaussianBlur(base, (0, 0), 2.0) * 3 - 255  # strong local contrast


def frame(scene: np.ndarray, dx: float, dy: float, h=200, w=260) -> np.ndarray:
    """What the camera sees with the slide offset (dx, dy): a slide point s shows at s + (dx, dy)."""
    x0, y0 = int(M - dx), int(M - dy)
    return scene[y0:y0 + h, x0:x0 + w].copy()


def clean(scene, dx, dy):
    return np.clip(frame(scene, dx, dy), 0, 255)


def test_overlap_is_what_every_shifted_frame_covers():
    assert enhance.overlap([(0, 0)], 100, 80) == (0, 0, 100, 80)
    # One frame moved right 5 and up 3 in the newest's coordinates: it covers x >= 5, y < 77.
    assert enhance.overlap([(5, -3), (0, 0)], 100, 80) == (5, 0, 100, 77)


def test_averaging_lowers_the_noise_and_aligns_by_the_slide_offset():
    scene = slide()
    rng = np.random.default_rng(7)
    offsets = [(i % 4, -(i % 3)) for i in range(16)]  # the slide drifted a few pixels
    sigma = 20.0
    frames = [(np.clip(frame(scene, dx, dy) + rng.normal(0, sigma, (200, 260)), 0, 255).astype(np.uint8), dx, dy)
              for dx, dy in offsets]
    result = enhance.average(frames, span_s=0.8)
    assert result.used == 16 and "averaged 16 real frames over 0.8 s" in result.words
    x0, y0 = result.origin
    truth = clean(scene, *offsets[-1])[y0:y0 + result.image.shape[0], x0:x0 + result.image.shape[1]]
    one = frames[-1][0][y0:y0 + result.image.shape[0], x0:x0 + result.image.shape[1]].astype(np.float32)
    single_error = np.abs(one - truth).mean()
    averaged_error = np.abs(result.image.astype(np.float32) - truth).mean()
    assert averaged_error < 0.4 * single_error  # about 1/sqrt(16) of the noise

    # Ignoring the offsets (averaging unaligned frames) smears the specimen: much worse.
    unaligned = enhance.average([(f, 0.0, 0.0) for f, _, _ in frames])
    ux0, uy0 = unaligned.origin
    unaligned_error = np.abs(unaligned.image.astype(np.float32) - clean(scene, *offsets[-1])[uy0:uy0 + unaligned.image.shape[0],
                             ux0:ux0 + unaligned.image.shape[1]]).mean()
    assert averaged_error < 0.5 * unaligned_error


def test_averaging_keeps_colour():
    scene = slide()
    gray = clean(scene, 0, 0).astype(np.uint8)
    bgr = cv2.merge([gray, gray // 2, 255 - gray])
    result = enhance.average([(bgr, 0.0, 0.0), (bgr, 0.0, 0.0)])
    assert result.image.shape == bgr.shape and np.abs(result.image.astype(int) - bgr).max() <= 1


def test_focus_stack_takes_each_half_from_the_frame_where_it_is_sharp():
    scene = slide()
    truth = clean(scene, 0, 0)
    blurred = cv2.GaussianBlur(truth, (0, 0), 4.0)
    half = truth.shape[1] // 2
    left_sharp = truth.copy()
    left_sharp[:, half:] = blurred[:, half:]
    right_sharp = blurred.copy()
    right_sharp[:, half:] = truth[:, half:]
    # The second frame was taken with the slide 3 px further right: aligned by its offset.
    shifted = np.clip(frame(scene, 3, 0), 0, 255)
    shifted_blurred = cv2.GaussianBlur(shifted, (0, 0), 4.0)
    right_sharp_moved = shifted_blurred.copy()
    right_sharp_moved[:, half + 3:] = shifted[:, half + 3:]

    frames = [(right_sharp_moved.astype(np.uint8), 3.0, 0.0), (left_sharp.astype(np.uint8), 0.0, 0.0)]
    result = enhance.focus_stack(frames)
    assert result.used == 2 and "focus-stacked 2 real frames" in result.words
    x0, y0 = result.origin
    h, w = result.image.shape[:2]
    out = result.image.astype(np.float32)
    want = truth[y0:y0 + h, x0:x0 + w]
    away = 12  # away from the seam, where the median-filtered choice settles
    left = slice(0, half - x0 - away)
    right = slice(half - x0 + away, w)
    for side in (left, right):
        stacked_error = np.abs(out[:, side] - want[:, side]).mean()
        blurred_error = np.abs(blurred[y0:y0 + h, x0:x0 + w][:, side] - want[:, side]).mean()
        assert stacked_error < 0.15 * blurred_error
    # Every pixel is a camera pixel from one of the frames, never a blend.
    a = frames[1][0][y0:y0 + h, x0:x0 + w]
    b = frames[0][0][y0:y0 + h, x0 + 3:x0 + 3 + w]  # the first frame, moved 3 px back into place
    assert np.all((result.image == a) | (result.image == b))


def test_sharpest_is_one_frame_unaltered():
    image = np.full((20, 30), 90, np.uint8)
    result = enhance.sharpest(image, 42, 3.0)
    assert result.used == 1 and result.image is image and "sharpest of 42 real frames" in result.words


def test_frames_that_do_not_overlap_are_refused():
    image = np.zeros((20, 20), np.uint8)
    with pytest.raises(ValueError):
        enhance.average([(image, 100.0, 0.0), (image, 0.0, 0.0)])


def test_focus_stack_scales_out_the_phone_refocusing_by_the_disc():
    """The phone's own lens refocused during the sweep: the older frame shows everything 2% larger
    about an optical centre that is not the disc's. Given each frame's disc (centre, radius), the
    stack scales it back and takes its sharp half in place; without them, that half is misplaced."""
    scene = slide()
    truth = clean(scene, 0, 0)
    h, w = truth.shape
    blurred = cv2.GaussianBlur(truth, (0, 0), 4.0)
    half = w // 2
    newest = blurred.copy()
    newest[:, half:] = truth[:, half:]  # right half sharp
    older_view = truth.copy()
    older_view[:, half:] = blurred[:, half:]  # left half sharp
    s, ox, oy = 1.02, 140.0, 90.0  # the phone's zoom and its optical centre (unknown to the app)
    zoom = np.float32([[s, 0, ox - s * ox], [0, s, oy - s * oy]])
    older = cv2.warpAffine(older_view, zoom, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    cx, cy, r = 125.0, 98.0, 80.0  # the newest frame's disc; the older one's grew about the same centre
    discs = [(ox + s * (cx - ox), oy + s * (cy - oy), s * r), (cx, cy, r)]
    frames = [(older.astype(np.uint8), 0.0, 0.0), (newest.astype(np.uint8), 0.0, 0.0)]

    def left_error(result):
        x0, y0 = result.origin
        hh, ww = result.image.shape[:2]
        side = slice(12, half - x0 - 12)
        return np.abs(result.image.astype(np.float32)[12:-12, side] - truth[y0:y0 + hh, x0:x0 + ww][12:-12, side]).mean()

    blurred_error = np.abs(blurred[:, 12:half - 12] - truth[:, 12:half - 12]).mean()
    assert left_error(enhance.focus_stack(frames, discs=discs)) < 0.25 * blurred_error
    assert left_error(enhance.focus_stack(frames)) > 0.5 * blurred_error  # misplaced by up to 3 px without
    # A disc that was not measured (radius 0) leaves the frame as it is.
    unmeasured = enhance.focus_stack(frames, discs=[(0.0, 0.0, 0.0), (cx, cy, r)])
    assert np.array_equal(unmeasured.image, enhance.focus_stack(frames).image)


def test_frames_given_as_loaders_give_the_same_picture_one_frame_resident_at_a_time():
    """Invariant 3: a focus stack of 30 full frames held at once was ~500 MB at 1080p. Given as loaders,
    frames are decoded when used; the result is identical, and no more than one is alive at once."""
    import gc
    import weakref

    from orchestrator.enhance import average, focus_stack

    rng = np.random.default_rng(3)
    images = [rng.integers(0, 255, (60, 80, 3), dtype=np.uint8) for _ in range(5)]
    frames = [(img, float(i), float(-i)) for i, img in enumerate(images)]
    alive = []

    def loader(i):
        def load():
            gc.collect()
            alive[:] = [r for r in alive if r() is not None]
            assert len(alive) <= 1, f"{len(alive)} frames resident at once"
            copy = images[i].copy()
            alive.append(weakref.ref(copy))
            return copy
        return load

    lazy = [(loader(i), dx, dy) for i, (_, dx, dy) in enumerate(frames)]
    assert np.array_equal(average(frames).image, average(lazy).image)
    alive.clear()
    assert np.array_equal(focus_stack(frames).image, focus_stack(lazy).image)


# --- the same pictures as before, held in less memory (finding 90) ---------------------------------------------------
def average_as_before(frames, span_s=None):
    """enhance.average as it was before finding 90 (a float copy of each frame, a new sum each time): the oracle the
    in-place average must equal pixel for pixel."""
    moved, (x0, y0, x1, y1) = enhance._aligned(frames)
    total = None
    for i in range(len(frames)):
        part = moved(i)[y0:y1, x0:x1].astype(np.float32)
        total = part if total is None else total + part
    return np.clip(total / len(frames) + 0.5, 0, 255).astype(np.uint8), (x0, y0)


@pytest.mark.parametrize("n, colour, shift, values", [
    (2, False, (0.0, 0.0), "noise"), (4, True, (1.0, -1.0), "noise"), (7, True, (0.5, 0.25), "noise"),
    (30, True, (0.37, -0.81), "noise"), (5, True, (2.0, 0.0), "white"), (6, False, (0.0, 0.0), "extremes"),
    (9, True, (0.0, 0.0), "halves"), (30, False, (1.0, 1.0), "halves")])
def test_an_average_is_the_same_picture_as_before_pixel_for_pixel(n, colour, shift, values):
    """The in-place sum is the same float32 arithmetic as the sum of float copies: every pixel, the origin and the
    words are what they were, whatever the frames (rounding at .5, all white, the extremes), shifts and count."""
    rng = np.random.default_rng(n)
    shape = (70, 90, 3) if colour else (70, 90)
    if values == "noise":
        images = [rng.integers(0, 256, shape, dtype=np.uint8) for _ in range(n)]
    elif values == "white":
        images = [np.full(shape, 255, np.uint8) for _ in range(n)]
    elif values == "extremes":
        images = [np.where(rng.random(shape) < 0.5, 0, 255).astype(np.uint8) for _ in range(n)]
    else:  # sums landing exactly on .5 after dividing: 0 and 1 alternating
        images = [np.full(shape, i % 2, np.uint8) for i in range(n)]
    frames = [(img, shift[0] * i, shift[1] * i) for i, img in enumerate(images)]
    result = enhance.average(frames, span_s=1.2)
    before, origin = average_as_before(frames)
    assert np.array_equal(result.image, before) and result.image.dtype == np.uint8 and result.origin == origin
    assert result.words == f"averaged {n} real frames over 1.2 s, aligned by the slide's movement"
    # The same frames cut to a box (copied out of their decodes) give the same picture as cut by hand.
    box = (5, 7, 80, 60)
    jpegs = [cv2.imencode(".png", img)[1].tobytes() for img in images]
    cut = enhance.average([(enhance._loader(j, box), dx, dy) for j, (_, dx, dy) in zip(jpegs, frames)])
    decoded = [cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR)[7:60, 5:80] for j in jpegs]
    assert np.array_equal(cut.image, average_as_before([(d, dx, dy) for d, (_, dx, dy) in zip(decoded, frames)])[0])


def focus_stack_as_before(frames, discs=None):
    """enhance.focus_stack as it was before finding 90 (np.where for the sharpness so far, a boolean index to copy each
    place): the oracle the in-place stack must equal pixel for pixel."""
    moved, (x0, y0, x1, y1) = enhance._aligned(frames, discs)
    best, choice, out = None, None, None
    for i in range(len(frames)):
        part = moved(i)[y0:y1, x0:x1]
        gray = part if part.ndim == 2 else cv2.cvtColor(part, cv2.COLOR_BGR2GRAY)
        e = enhance.energy(gray)
        if best is None:
            best, choice = e, np.zeros(e.shape, np.uint8)
            out = np.empty_like(part)
        else:
            better = e > best
            best = np.where(better, e, best)
            choice[better] = i
    choice = cv2.medianBlur(choice, enhance.STACK_MEDIAN)
    for i in range(len(frames)):
        mask = choice == i
        if mask.any():
            out[mask] = moved(i)[y0:y1, x0:x1][mask]
    return out, (x0, y0), len(np.unique(choice))


@pytest.mark.parametrize("n, colour, sharp, scaled", [
    (2, False, "halves", False), (5, True, "bands", False), (5, True, "bands", True), (9, True, "one", False),
    (12, False, "bands", True), (30, True, "bands", True), (4, True, "ties", False)])
def test_a_focus_stack_is_the_same_picture_as_before_pixel_for_pixel(n, colour, sharp, scaled):
    """Filled in place, the stack chooses the same frame for every place and copies the same pixels as before: every
    pixel, the origin and the words, whether each frame is sharp in a band, one frame is sharp everywhere, frames tie
    (the first keeps the place), or the phone's lens breathed (frames scaled by their discs)."""
    scene = clean(slide(seed=n), 0, 0).astype(np.uint8)
    rng = np.random.default_rng(n)
    blurred = cv2.GaussianBlur(scene, (0, 0), 3.0)
    images = []
    for i in range(n):
        img = blurred.copy()
        if sharp == "halves":
            half = img.shape[1] // 2
            img[:, i * half:(i + 1) * half] = scene[:, i * half:(i + 1) * half]
        elif sharp == "bands":
            band = img.shape[0] // n + 1
            img[i * band:(i + 1) * band] = scene[i * band:(i + 1) * band]
        elif sharp == "ties":  # every frame the same but for its own sharp band (frame 0 has none): ties elsewhere
            band = img.shape[0] // n + 1
            if i:
                img[i * band:(i + 1) * band] = scene[i * band:(i + 1) * band]
        elif i == n // 2:  # "one": a single frame sharp everywhere, the rest blurred
            img = scene.copy()
        if sharp != "ties":  # (ties keep the frames' equal places equal: the first frame keeps them, as before)
            img = np.clip(img.astype(np.int16) + rng.integers(-3, 4, img.shape), 0, 255).astype(np.uint8)
        images.append(cv2.merge([img, img // 2, 255 - img]) if colour else img)
    still = sharp == "ties"  # the same places in every frame: a tie at each of them
    frames = [(img, 0.0 if still else float(i % 3), 0.0 if still else -float(i % 2)) for i, img in enumerate(images)]
    discs = [(125.0, 98.0, 80.0 * (1.0 + 0.01 * np.sin(i))) for i in range(n)] if scaled else None
    result = enhance.focus_stack(frames, ago_s=4.0, discs=discs)
    before, origin, used = focus_stack_as_before(frames, discs)
    assert np.array_equal(result.image, before) and result.origin == origin
    assert used == n or not still  # a tie stays with the first frame: frame 0 contributes the places where all agree
    assert result.words == (f"focus-stacked {n} real frames from the last turn of the focus knob, 4.0 s ago (each place "
                            f"taken from the frame where it was sharpest; {used} of them contributed)")


def test_a_cut_to_the_eyepiece_lets_the_whole_frames_decode_go(monkeypatch):
    """#186 re-check SF1: the cut was a view, so each frame's whole decode stayed alive behind it. It is a copy now."""
    import gc
    import weakref

    decodes = []
    real = enhance.decode

    def tracked(jpeg):
        image = real(jpeg)
        decodes.append(weakref.ref(image))
        return image

    monkeypatch.setattr(enhance, "decode", tracked)
    jpeg = cv2.imencode(".jpg", np.full((90, 120, 3), 100, np.uint8))[1].tobytes()
    cut = enhance._loader(jpeg, (10, 20, 60, 70))()
    gc.collect()
    assert cut.shape == (50, 50, 3) and cut.base is None
    assert len(decodes) == 1 and decodes[0]() is None  # the whole frame's decode is gone; the cut is its own


# --- what making a picture holds stays inside what it reserves (contract 1, invariant 3; finding 90) -----------------
T0 = 1_760_000_000.0
SIZES = {"720p": (1280, 720, 330), "1080p": (1920, 1080, 450)}


def camera_pictures(w, h, fov, k=4):
    """A few camera-like JPEGs (texture, cells, noise, the dark surround of the eyepiece), cycled through a feed."""
    rng = np.random.default_rng(w)
    base = cv2.resize(rng.uniform(0, 255, (h // 4, w // 4)).astype(np.float32), (w, h))
    bgr = cv2.merge([base * 0.8, base * 0.6 + 40, 255 - base * 0.7])
    for _ in range(40):
        cv2.circle(bgr, (int(rng.integers(0, w)), int(rng.integers(0, h))), int(rng.integers(8, 40)), (90.0, 120.0, 180.0), -1)
    yy, xx = np.ogrid[:h, :w]
    outside = (xx - fov.cx) ** 2 + (yy - fov.cy) ** 2 > fov.radius ** 2 if fov is not None else np.zeros((h, w), bool)
    out = []
    for i in range(k):
        img = bgr + rng.normal(0, 10, bgr.shape).astype(np.float32)
        if i % 2:
            img = cv2.GaussianBlur(img, (0, 0), 1.0 + i)
        img[outside] = 8
        out.append(cv2.imencode(".jpg", np.clip(img, 0, 255).astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes())
    return out


def live_hub(w, h, fov, n=40, sweep=False, one_sharp=False):
    """A real hub fed n frames of a still (slightly drifting) slide; with `sweep`, a turn of the focus knob during
    which the phone's lens breathed (each frame's disc a little different: the stack scales them back). `one_sharp`:
    the frames in the middle of the turn are one and the same sharp picture and the rest blurred, so one frame wins
    every place (the most a stack once held: index arrays as large as the picture)."""
    from orchestrator.frames import Frame
    from orchestrator.hub import FrameInfo, Hub
    from orchestrator.sweep import SweepBuffer

    hub = Hub("http://127.0.0.1:1", use_gpu=False)
    hub.scale_reading = lambda: None
    buffer = SweepBuffer(hub.source.get, clock=lambda: T0 + 10) if sweep else None
    pictures = camera_pictures(w, h, fov)
    infos = []
    for k in range(n):
        i = k + 1
        picture = (pictures[0] if abs(k - n // 2) < 10 else pictures[1 + 2 * (k % 2)]) if one_sharp else pictures[k % len(pictures)]
        hub.source.ring[i] = Frame(i, picture, 0.0, T0 + i / 30)
        focus = 100.0 + 400.0 * np.sin(np.pi * k / n) if sweep else 100.0
        info = FrameInfo(i, w, h, float(k % 3), -float(k % 2), 0, True, True, fov, T0 + i / 30, focus)
        info.disc = fov.radius * (1.0 + 0.004 * np.sin(k / 7.0)) if fov is not None else 0.0
        hub.infos[i] = info
        hub.latest_info = info
        if buffer is not None:
            buffer.frame(info)
        infos.append(info)
    hub.source.is_fresh = lambda: True
    return hub, buffer, infos


def held_peaks(memory):
    """Each reservation made in `memory` while tracing: what it reserved, and what was held above where it began while
    it was held: tracemalloc's peak, or a frame's decode with what was traced as it began, whichever is more (`held`),
    and what was still allocated from then when it was released (`left`). A decode holds twice the array it returns
    at once: OpenCV's own buffer, which tracemalloc can't see, and the array (measured with malloc's own counts:
    finding 90)."""
    import tracemalloc

    seen = Seen()
    real_reserve, real_decode = memory.reserve, enhance.decode

    def reserve(cls, nbytes, why):
        reservation = real_reserve(cls, nbytes, why)
        base = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        record = {"cls": cls, "nbytes": int(nbytes), "base": base, "decoding": 0}
        seen.append(record)
        release = reservation.release

        def measured():
            if "peak" not in record:
                now, peak = tracemalloc.get_traced_memory()
                record.update(peak=peak - base, left=now - base, held=max(peak - base, record["decoding"]))
            release()
        reservation.release = measured
        return reservation

    def decode(jpeg):
        before = tracemalloc.get_traced_memory()[0]
        image = real_decode(jpeg)
        for record in seen:
            if "peak" not in record:
                record["decoding"] = max(record["decoding"], before - record["base"] + 2 * image.nbytes)
        return image

    memory.reserve = reserve
    enhance.decode = decode
    seen.restore = lambda: setattr(enhance, "decode", real_decode)
    return seen


class Seen(list):
    pass


def make_picture(path, how, size, fov=True, tmp_path=None, one_sharp=False):
    """One picture made through the real code, the user's tool or a researcher's hands, while tracing: its
    reservations (held_peaks) and the picture made."""
    import asyncio
    import tracemalloc

    from orchestrator.brain.registry import ToolContext
    from orchestrator.brain.toolbox import Services, build
    from orchestrator.capture import captures
    from orchestrator.fov import FieldOfView
    from orchestrator.magnifier import Magnifier
    from orchestrator.research.hands import Hands
    from tests.test_research_samples import DAY, clip_of, results_of, session

    w, h, r = SIZES[size]
    disc = FieldOfView(w / 2, h / 2, r, 1.0) if fov else None
    hub, buffer, infos = live_hub(w, h, disc, n=90 if how == "stack" else 40, sweep=how == "stack", one_sharp=one_sharp)
    seen = held_peaks(captures(hub).memory)
    tracemalloc.start()
    try:
        if path == "user":
            magnifier = Magnifier(hub)
            registry = build(Services(hub=hub, magnifier=magnifier, sweep=buffer, snapshots=None))

            async def go():
                run = registry.start("enhance", {"how": how}, ToolContext("chat", 1, None, ""))
                await run.task
                return run
            run = asyncio.run(go())
            assert run.ok, run.summary
            picture = magnifier.pictures[magnifier.sources[-1]["url"].rsplit("/", 1)[1]]
        else:
            store = results_of(hub, tmp_path)
            hands = Hands(hub)
            hands.sweep = buffer
            kw = {"input": clip_of(hub, infos[-12:]).id} if path == "held" else {}
            reply = asyncio.run(hands.run(session(day=DAY), "enhance", "x", how=how, **kw))
            assert reply["ok"], reply
            picture = store.picture(reply["id"], 0, DAY)
    finally:
        tracemalloc.stop()
        seen.restore()
    return seen, picture


CASES = [("user", "average"), ("user", "sharpest"), ("user", "stack"), ("live", "average"), ("live", "sharpest"),
         ("live", "stack"), ("held", "average"), ("held", "sharpest")]


@pytest.mark.parametrize("path, how, size", [(p, h, s) for s in SIZES for p, h in CASES
                                             if (p, h, s) != ("live", "stack", "1080p")])  # refused: below
def test_what_making_a_picture_holds_stays_inside_what_it_reserves(path, how, size, tmp_path):
    """#186 re-check SF1 and note 5: an average held 1.8 times what it reserved (the cut a view of the whole decode,
    three float images at once), so a researcher's at 1080p was admitted under its 24 MB and then went over it.
    Measured with tracemalloc over the real code, the peak while it is made is inside its reservation, for the user's
    tool (the analysis allowance, the whole frame) and a researcher's hands (its own allowance, the eyepiece alone),
    and its pixels are let go before the reservation is: only the JPEG it made is left."""
    seen, picture = make_picture(path, how, size, tmp_path=tmp_path)
    assert len(seen) == 1 and seen[0]["cls"] == ("analysis" if path == "user" else "research")
    record = seen[0]
    assert record["decoding"] > 0 and record["peak"] > 0  # frames were decoded inside the reservation
    assert record["held"] <= record["nbytes"], f"held {record['held'] / 2**20:.2f} MB of {record['nbytes'] / 2**20:.2f} MB"
    assert record["left"] <= len(picture) + 256 * 1024  # the picture's pixels went inside the reservation


@pytest.mark.parametrize("how", ["average", "sharpest"])
def test_a_held_clip_without_an_eyepiece_holds_inside_what_it_reserves_for_the_whole_frame(how, tmp_path):
    """No eyepiece disc measured: a researcher's picture of a held clip works on the whole frame, and holds inside that."""
    seen, picture = make_picture("held", how, "720p", fov=False, tmp_path=tmp_path)
    assert len(seen) == 1 and 0 < seen[0]["held"] <= seen[0]["nbytes"] and seen[0]["left"] <= len(picture) + 256 * 1024


@pytest.mark.parametrize("size", ["720p", "1080p"])
def test_a_stack_one_frame_wins_everywhere_holds_inside_what_it_reserves(size, tmp_path):
    """The stack's copying once took each place by a boolean index, whose index arrays (16 bytes a place taken) made
    its peak depend on how much one frame won: with one frame sharp everywhere it went past its reservation. Filled
    in place, it holds the same whatever the frames."""
    seen, picture = make_picture("user", "stack", size, fov=False, tmp_path=tmp_path, one_sharp=True)
    assert len(seen) == 1 and 0 < seen[0]["held"] <= seen[0]["nbytes"] and seen[0]["left"] <= len(picture) + 256 * 1024


def test_what_a_researchers_picture_cannot_fit_is_refused_with_what_it_needs_and_nothing_reserved(tmp_path):
    """A focus stack is never cut to the eyepiece, so at 1920x1080 a researcher's needs more than its allowance: the
    refusal says what it needs (its reservation, in MB) and what researchers have, and nothing is reserved."""
    import asyncio

    from orchestrator.capture import MB, captures
    from orchestrator.enhance import live_plan
    from orchestrator.fov import FieldOfView
    from orchestrator.research.hands import Hands
    from tests.test_research_samples import DAY, results_of, session

    w, h, r = SIZES["1080p"]
    hub, buffer, _ = live_hub(w, h, FieldOfView(w / 2, h / 2, r, 1.0), n=90, sweep=True)
    results_of(hub, tmp_path)
    seen = held_peaks(captures(hub).memory)
    hands = Hands(hub)
    hands.sweep = buffer
    try:
        reply = asyncio.run(hands.run(session(day=DAY), "enhance", "x", how="stack"))
    finally:
        seen.restore()
    need = live_plan(hub, "stack", buffer, crop=True).need
    assert need > captures(hub).memory.allowances["research"] and not seen
    assert reply["error"] == f"a clearer picture of this 1920x1080 view needs {need / MB:.0f} MB, and researchers' work has 24 MB"
