"""What moves on the slide, measured against dots whose motion is known (orchestrator/movement.py)."""
import numpy as np
import cv2

from orchestrator.movement import ClipFrame, follow, measure, trails, words


def clip(movers, still, seconds=3.0, fps=30, slide=(0.0, 0.0), noise=4.0, seed=1):
    """Frames with dark dots: `movers` [(x, y, vx, vy) px and px/s], `still` [(x, y)]. The
    whole slide also moves at `slide` px/s, and the frames say so in dx, dy (as the hub does)."""
    rng = np.random.default_rng(seed)
    frames = []
    for i in range(int(seconds * fps)):
        t = i / fps
        img = np.full((720, 1280), 150, np.float32) + rng.normal(0, noise, (720, 1280))
        sx, sy = slide[0] * t, slide[1] * t
        for x, y, vx, vy in movers:
            cv2.circle(img, (int(round(x + vx * t + sx)), int(round(y + vy * t + sy))), 5, 60, -1)
        for x, y in still:
            cv2.circle(img, (int(round(x + sx)), int(round(y + sy))), 5, 60, -1)
        frames.append(ClipFrame(t, np.clip(img, 0, 255).astype(np.uint8), sx, sy))
    return frames


MOVERS = [(300, 200, 60, 0), (500, 500, 0, -40), (900, 300, -30, 30), (700, 600, 45, 10), (400, 400, 25, 25)]
STILL = [(200, 600), (1000, 550), (600, 150), (1100, 200)]


def test_movers_and_still_things_are_told_apart_with_their_speeds():
    frames = clip(MOVERS, STILL)
    m = measure(frames, follow(frames), um_per_px=None)
    assert len(m.counted) == len(MOVERS) + len(STILL)
    assert len(m.moving) == len(MOVERS)
    truth = sorted(np.hypot(vx, vy) for _, _, vx, vy in MOVERS)
    got = sorted(c["net"] for c in m.moving)
    for want, have in zip(truth, got):
        assert abs(have - want) / want < 0.12, (want, have)
    assert all(c["straightness"] > 0.9 for c in m.moving)


def test_moving_the_slide_is_not_counted_as_the_things_moving():
    frames = clip(MOVERS, STILL, slide=(80.0, -20.0))
    m = measure(frames, follow(frames), um_per_px=None)
    assert len(m.moving) == len(MOVERS)
    assert m.slide_speed > 70  # but it is said: the view as a whole moved
    assert "The whole view moved" in words(m)


def test_a_shared_drift_is_reported_as_a_fact():
    drifting = [(200 + 150 * i, 300 + 60 * (i % 3), 50, -2) for i in range(6)]
    frames = clip(drifting, STILL)
    m = measure(frames, follow(frames), um_per_px=None)
    assert m.drift is not None and "right" in words(m)


def test_speeds_use_the_scale_when_there_is_one():
    frames = clip(MOVERS[:1], [])
    m = measure(frames, follow(frames), um_per_px=0.5)
    assert m.unit == "µm/s" and abs(m.moving[0]["net"] - 30) < 4
    assert "µm/s" in words(m, "40x objective (estimate)")


def test_nothing_to_follow_says_so_and_the_trails_picture_draws():
    frames = clip([], [])
    m = measure(frames, follow(frames), um_per_px=None)
    assert "nothing was followed" in words(m)
    frames = clip(MOVERS, STILL)
    m = measure(frames, follow(frames), um_per_px=None)
    bgr = cv2.cvtColor(frames[-1].image, cv2.COLOR_GRAY2BGR)
    drawn = trails(bgr, m, (frames[-1].dx - frames[0].dx, frames[-1].dy - frames[0].dy))
    assert drawn.shape == bgr.shape and (drawn != bgr).any()


def _hub_with(frames, epoch_break_at=None):
    """A hub holding these frames as the live one does: JPEGs in the source, offsets in infos."""
    from collections import OrderedDict
    from types import SimpleNamespace

    from tests.fakes import Hub

    hub = Hub()
    store, infos = {}, OrderedDict()
    for i, f in enumerate(frames):
        ok, jpeg = cv2.imencode(".jpg", f.image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        store[i] = SimpleNamespace(id=i, jpeg=jpeg.tobytes())
        epoch = 1 if epoch_break_at is not None and i >= epoch_break_at else 0
        infos[i] = SimpleNamespace(id=i, wall_at=100.0 + f.t, dx=f.dx, dy=f.dy, epoch=epoch, fov=None)
    hub.source = SimpleNamespace(get=store.get, is_fresh=lambda: True)
    hub.infos = infos
    hub.latest_info = infos[len(frames) - 1]
    hub.scale_reading = lambda: None
    return hub


def _run(hub, args, magnifier=None, log=None):
    import asyncio

    from orchestrator.brain.registry import ToolContext
    from orchestrator.brain.toolbox import Services, build

    registry = build(Services(hub=hub, magnifier=magnifier, log=log))

    async def go():
        run = registry.start("track_motion", args, ToolContext("voice", 1, 7, "are they swimming?"))
        await run.task
        return run

    return registry, asyncio.run(go())


def test_the_tool_measures_the_frames_just_seen_and_draws_the_tracks():
    from types import SimpleNamespace

    shown, logged = [], []
    magnifier = SimpleNamespace(show_picture=lambda jpeg, label, object_id=None: shown.append(label))
    registry, run = _run(_hub_with(clip(MOVERS, STILL, seconds=4.0)), {"live": False}, magnifier, log=logged.append)
    assert registry.tools["track_motion"].kind == "query" and registry.tools["track_motion"].family == "motion"
    assert run.ok, run.summary
    assert f"{len(MOVERS)} of them" in run.summary and "px/s" in run.summary and "No scale is set" in run.summary
    assert shown and "drawn by the app" in shown[0]
    # The numbers themselves are logged (datasets), not only said: per track, in the unit it was measured in.
    motion = [r for r in logged if r.get("kind") == "measurement"][-1]
    assert motion["what"] == "motion" and motion["unit"] == "px/s" and motion["moving"] == len(MOVERS)
    assert motion["counted"] and {"net", "path"} <= set(motion["counted"][0]) and motion["scale"] is None


def test_frames_from_before_the_slide_whipped_away_are_not_used():
    hub = _hub_with(clip(MOVERS, STILL, seconds=4.0), epoch_break_at=110)  # only ~0.3 s since the break
    _, run = _run(hub, {"seconds": 1.5, "show": False})
    assert run.ok is False and "hold it still" in run.summary
