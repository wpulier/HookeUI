"""Time: the last minute of the view (clip.py), what happened in it (what_happened), and
watching for something to happen (watcher.py, watch)."""
import asyncio
from types import SimpleNamespace

import cv2
import numpy as np

from orchestrator.brain.registry import ToolContext
from orchestrator.brain.toolbox import Services, build
from orchestrator.clip import ClipBuffer
from orchestrator.watcher import Watcher

JPEG = cv2.imencode(".jpg", np.full((72, 128, 3), 120, np.uint8))[1].tobytes()


def info(i, t, dx=0.0, epoch=0, focus=1.0):
    return SimpleNamespace(id=i, wall_at=t, dx=dx, dy=0.0, epoch=epoch, steady=True, focus=focus, fov=None)


def filled(seconds=70.0, fps=30):
    clip = ClipBuffer(lambda fid: SimpleNamespace(jpeg=JPEG))
    for i in range(int(seconds * fps)):
        clip.frame(info(i, 1000.0 + i / fps, dx=0.0 if i < 1500 else 200.0))
    return clip


def test_the_clip_keeps_a_frame_every_half_second_for_a_minute():
    clip = filled()
    times = [f.wall_at for f in clip.frames]
    assert 115 <= len(times) <= 122 and times[-1] - times[0] <= 60.0
    assert all(b - a >= 0.49 for a, b in zip(times, times[1:]))
    picked = clip.window(30.0, 8)
    assert len(picked) == 8 and picked[-1] is clip.frames[-1] and picked[-1].wall_at - picked[0].wall_at <= 30.0


def test_what_happened_shows_the_frames_in_order_with_what_the_slide_did():
    clip = filled()
    asked = {}

    async def ask(images, prompt, max_output_tokens=0):
        asked.update(images=images, prompt=prompt)
        return SimpleNamespace(data={"answer": "A cell swam in from the left.", "changed": True}, error="")

    from tests.fakes import Hub

    hub = Hub()
    agent = SimpleNamespace(vision=lambda option: SimpleNamespace(ask=ask), snapshot=None)
    registry = build(Services(hub=hub, agent=agent, clip=clip))

    async def go():
        from orchestrator.ticket import issue, using

        request = issue(hub, "turn")
        try:
            with using(request):
                run = registry.start("what_happened", {"seconds": 30}, ToolContext("voice", 1, 7, "what was that?"))
                await run.task
                return run
        finally:
            request.seal()

    run = asyncio.run(go())
    assert run.ok and "A cell swam in" in run.summary and registry.tools["what_happened"].kind == "query"
    assert len(asked["images"]) == 8 and "Frame 8: 0 s ago" in asked["prompt"]
    assert "the slide moved 200 px" in asked["prompt"] and '"what was that?"' in asked["prompt"]


def watcher(shares=(), looks=(), said=None):
    shares, looks = list(shares), list(looks)

    async def measure():
        return (shares.pop(0), f"{len(shares)} left") if shares else None

    async def ask(question):
        return looks.pop(0) if looks else None

    async def no_wait(_s):
        await asyncio.sleep(0)

    said = said if said is not None else []
    return Watcher(measure, ask, lambda w, text, fired: said.append((text, fired)), sleep=no_wait), said


def test_motion_fires_when_the_share_stays_below_twice():
    async def go():
        w, said = watcher(shares=[0.6, 0.1, 0.5, 0.1, 0.05])
        w.start("motion", "they stop moving", below=0.2)
        for _ in range(50):
            await asyncio.sleep(0)
        return w, said

    w, said = asyncio.run(go())
    assert said and said[0][1] is True and "They stop moving" in said[0][0] and not w.active


def test_appears_fires_on_new_content_and_not_on_light():
    async def go():
        w, said = watcher()
        w.start("appears", "something swims in")
        await asyncio.sleep(0)
        w.appeared("light")
        await asyncio.sleep(0)
        assert not said
        w.appeared("content")
        for _ in range(5):
            await asyncio.sleep(0)
        return said

    said = asyncio.run(go())
    assert said == [("Something new appeared in the view (something swims in).", True)]


def test_look_fires_on_the_models_yes_and_a_watch_can_be_stopped():
    """Synthetic external look replies; the real watcher reports firing and cancellation once."""
    async def go():
        w, said = watcher(looks=[(False, "one cell"), (True, "two daughter cells")])
        first = w.start("look", "the cell divides", question="Has the cell divided into two?")
        for _ in range(20):
            await asyncio.sleep(0)
        w2 = w.start("look", "it bursts", question="Has it burst?")
        assert "it bursts" in w.describe()
        assert [x.what for x in w.stop("bursts")] == ["it bursts"] and not w.active
        await asyncio.gather(w2.task, return_exceptions=True)
        assert w.stop("bursts") == [] and not w.active  # no second terminal delivery
        return said, first, w2

    said, first, stopped = asyncio.run(go())
    assert said == [("The cell divides: two daughter cells", True),
                    ("Stopped watching for it bursts: cancelled by you.", False)]
    assert first.event == "fired" and first.checks == 2 and first.last == "two daughter cells"
    assert first.facts()["observation"] == {"text": "two daughter cells", "source": "model"}
    assert stopped.event == "cancelled" and stopped.checks == 0 and stopped.seconds >= 0
    assert stopped.task.done() and stopped.facts()["fired"] is False


def test_a_watch_that_runs_out_of_time_says_so(monkeypatch):
    clock = [0.0]

    async def go():
        w, said = watcher(shares=[0.9] * 100)
        w.clock = lambda: clock[0]

        async def tick(_s):
            clock[0] += 5.0
            await asyncio.sleep(0)

        w.sleep = tick
        w.start("motion", "they stop moving", below=0.2, seconds=30)
        for _ in range(200):
            await asyncio.sleep(0)
        return said

    said = asyncio.run(go())
    assert said and said[0][1] is False and "Stopped watching for they stop moving" in said[0][0]


def test_the_watch_tool_starts_stops_and_refuses_what_it_cannot_watch():
    w, said = watcher(shares=[0.9] * 1000)
    from tests.fakes import Hub

    registry = build(Services(hub=Hub(), watcher=w))

    async def go():
        async def run(name, args):
            r = registry.start(name, args, ToolContext("voice", 1, 7, "tell me when they stop moving", "d1"))
            await r.task
            return r

        bad = await run("watch", {"what": "they stop", "by": "motion"})
        ok = await run("watch", {"what": "they stop moving", "by": "motion", "below": 0.2})
        stop = await run("stop_watching", {})
        return bad, ok, stop

    bad, ok, stop = asyncio.run(go())
    assert bad.ok is False and "below or above" in bad.summary
    assert ok.ok and "watching for they stop moving" in ok.summary
    assert stop.ok and "stopped watching for they stop moving" in stop.summary
