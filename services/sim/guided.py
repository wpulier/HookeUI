"""Guiding the user's hands, end to end on the simulated camera (orchestrator/guide.py; docs/simulation.md).

The camera bridge plays a virtual microscope (sim/virtual_scope.py: the labelled frame of the qualified leaf recording
as the slide, out of focus, a stoma off to one side) as the camera, through its own FFmpeg capture (`POST /sim/stage`);
the orchestrator, the models and the studio see a camera. A simulated person reads each guidance line from the
conversation (the chat's app notices, as a person reads the chat or hears the voice) and moves the virtual slide and
focus the way it asks, with a hand's speed and imprecision. The truth (where the stoma is, how far from sharp) is the
virtual scope's, read only here.

- `guide`: the stoma is outlined with a click at its labelled place, then guided to with option GUIDE "rule" (no model
  call, so it runs in the gate).
- `guide-words` (cloud): Will's words, "find me a stoma and centre it", typed to the brain with the default judge:
  the brain finds one, then guides to it.
"""
from __future__ import annotations

import asyncio
import math
import time

AMOUNT_R = {"nudge": 0.1, "little": 0.22, "some": 0.45, "far": 0.85}  # what the simulated hand means by each word
FOCUS_STEP = 0.5  # z units a focus line turns the knob (half for "a touch")
HAND = {"right": (1.0, 0.0), "left": (-1.0, 0.0), "away": (0.0, -1.0), "toward": (0.0, 1.0)}
REACT_S = 0.6
CENTRED_R = 0.15
SHARP_BLUR = 0.9  # blur sigma (slide px) at which the bench's own sharpness reads about 85% of sharp


class Person:
    """Reads guidance lines from the viewer's app notices and moves the virtual scope as asked."""

    def __init__(self, viewer, scope, seed: int = 5) -> None:
        import numpy as np

        self.viewer, self.scope = viewer, scope
        self.rng = np.random.default_rng(seed)
        self.seen = viewer.notice_count
        self.lines: list[dict] = []
        self.ended: dict | None = None
        self.target: str | None = None

    def _follow(self, line: dict) -> None:
        say = (line.get("say") or "").lower()
        if say.startswith("stop"):
            self.scope.halt()
        slide = line.get("slide") or {}
        x = HAND.get(str(slide.get("x") or ""), (0.0, 0.0))
        y = HAND.get(str(slide.get("y") or ""), (0.0, 0.0))
        hx, hy = x[0] + y[0], x[1] + y[1]
        n = math.hypot(hx, hy)
        if n > 0:
            radii = AMOUNT_R.get(str(slide.get("amount") or "little"), 0.22) * float(self.rng.uniform(0.6, 1.4))
            dist = radii * self.scope.r / self.scope.zoom  # slide px for that much picture movement
            self.scope.hand(hx / n * dist, hy / n * dist, after=REACT_S)
        if line.get("focus") in ("up", "down"):
            small = any(w in say for w in ("touch", "hair", "tiny", "slightly", "back a little"))
            dz = FOCUS_STEP * (0.5 if small else 1.0) * float(self.rng.uniform(0.7, 1.3))
            self.scope.hand(dz=dz if line["focus"] == "up" else -dz, after=REACT_S)

    async def run(self, timeout: float) -> dict | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            fresh = [n for i, n in self.viewer.notices if i > self.seen]
            if fresh:
                self.seen = max(i for i, _ in self.viewer.notices)
            for notice in fresh:
                if (notice.get("producer") or {}).get("kind") != "guide":
                    continue
                line = (notice.get("facts") or {}).get("guide") or {}
                self.target = self.target or line.get("target")
                self.lines.append(line)
                if line.get("event") == "line":
                    self._follow(line)
                else:
                    self.ended = line
                    return line
            await asyncio.sleep(0.05)
        return None


async def _on_stage(stack, viewer, out, scope) -> bool:
    """The bridge plays the virtual scope, and the viewer has the stage's own frames, tracked and still: frames numbered
    after the switch, with the stage's eyepiece circle (a frame of the footage before it can still be in flight)."""
    reply = await stack.http.post(f"{stack.bridge_url}/sim/stage", params={"url": f"{scope.url}/stream.mjpeg"})
    if not out.check("the bridge plays the virtual stage as the camera", reply.status_code == 200 and reply.json().get("ok"),
                     reply.text[:200]):
        return False
    switched = (await stack.sim_status()).get("latestBridgeFrame") or 0
    deadline, still_since = time.monotonic() + 25, None
    while time.monotonic() < deadline:
        _, header, _ = viewer.latest()
        fov = header.get("fov") or [0, 0, 0]
        ours = ((header.get("seq") or 0) > switched and abs(fov[0] - scope.cx) < 12 and abs(fov[1] - scope.cy) < 12
                and header.get("tracked") and header.get("steady"))
        still_since = (still_since or time.monotonic()) if ours else None
        if still_since is not None and time.monotonic() - still_since >= 1.5:
            return out.check("the orchestrator tracks the stage's own view, still", True)
        await asyncio.sleep(0.05)
    _, header, _ = viewer.latest()
    return out.check("the orchestrator tracks the stage's own view, still", False,
                     str({k: header.get(k) for k in ("seq", "fov", "tracked", "steady", "epoch")}))


async def _put_back(stack, before: dict) -> None:
    await stack.http.post(f"{stack.bridge_url}/feed", params={"source": "footage", "footage": before.get("footage"),
                                                             "at": (before.get("playback") or {}).get("seconds") or 0})
    await stack.wait(lambda h: h["state"] == "live", 15, "live on the footage it found")


def _truth(out, scope, stoma, line: dict | None) -> None:
    px, py = scope.where(*stoma)
    r = math.hypot(px - scope.cx, py - scope.cy) / scope.r
    blur = scope.truth()["blur"]
    out.observations.append({"truth": {"fromCentre": round(r, 3), "blur": round(blur, 2)}, "ended": line})
    out.check("the guidance ended done", bool(line) and line.get("event") == "done", str(line)[:300])
    out.check(f"the stoma is centred (truth: {r:.2f} of the radius from the centre)", r <= CENTRED_R)
    out.check(f"the stoma is sharp (truth: blur {blur:.2f} px)", blur <= SHARP_BLUR)


async def scenario_guide(stack, viewer):
    """A stoma outlined by a click, guided to the centre and into focus by the rule (no model call)."""
    from .harness import Outcome, default_video
    from .virtual_scope import leaf_scope

    out = Outcome("guide: a stoma off to one side and out of focus is centred and made sharp, line by line")
    if stack.video.resolve() != default_video().resolve():
        out.check("the labelled leaf recording is the stack's footage (its stomata are labelled)", False, str(stack.video))
        return out
    before = (await stack.http.get(f"{stack.bridge_url}/feed")).json()
    scope, stoma = leaf_scope(stack.video, target="right", off=(0.45, -0.3), z=0.9)
    scope.serve()
    settings_before = dict(viewer.settings)
    try:
        if not await _on_stage(stack, viewer, out, scope):
            return out
        await viewer.send({"type": "settings", "settings": {"guide": "rule"}})
        _, header, _ = viewer.latest()
        px, py = scope.where(*stoma)
        since = len(viewer.results)
        await viewer.send({"type": "click", "frameId": header["id"], "x": px, "y": py, "identify": False})
        reply = await viewer.result_for("click", since, timeout=20)
        if not out.check("a click on the stoma outlines it", bool(reply and reply.get("ok")), str(reply)[:200]):
            return out
        since = len(viewer.results)
        await viewer.send({"type": "tool", "name": "where_is", "args": {"object": reply["objectId"]}})
        where = await viewer.result_for("tool", since)
        out.observations.append({"where_is": where, "scene": viewer.scene.get(reply["objectId"]),
                                 "frame": {k: header.get(k) for k in ("id", "epoch", "dx", "dy", "tracked", "steady", "fov")}})
        person = Person(viewer, scope)
        follow = asyncio.create_task(person.run(120))
        since = len(viewer.results)
        await viewer.send({"type": "tool", "name": "guide", "args": {"object": reply["objectId"], "what": "stoma"}})
        started = await viewer.result_for("tool", since, timeout=15)
        out.check("guide starts and its answer carries the first line", bool(started and started.get("ok"))
                  and "First:" in (started.get("summary") or ""), str(started)[:300])
        line = await follow
        out.observations.append({"lines": [x.get("say") for x in person.lines]})
        _truth(out, scope, stoma, line)
        out.check("every line is short and plain (at most 10 words, no ids)",
                  all(len((x.get("say") or "").split()) <= 10 and "o1" not in (x.get("say") or "") for x in person.lines),
                  str([x.get("say") for x in person.lines]))
        health = await stack.orch_health()
        out.check("the live view kept going throughout", health.get("state") == "live", str(health.get("state")))
    finally:
        await viewer.send({"type": "tool", "name": "stop_guiding", "args": {}})
        if settings_before.get("guide"):
            await viewer.send({"type": "settings", "settings": {"guide": settings_before["guide"]}})
        await _put_back(stack, before)
        scope.close()
    return out


async def scenario_guide_words(stack, viewer):
    """Will's words, typed: "find me a stoma and centre it". The brain finds one and guides to it with the default
    judge (the fast model)."""
    from .harness import Outcome, default_video
    from .virtual_scope import LEAF_STOMATA, leaf_scope

    out = Outcome("guide in Will's words: \"find me a stoma and centre it\"")
    if stack.video.resolve() != default_video().resolve():
        out.check("the labelled leaf recording is the stack's footage (its stomata are labelled)", False, str(stack.video))
        return out
    before = (await stack.http.get(f"{stack.bridge_url}/feed")).json()
    scope, stoma = leaf_scope(stack.video, target="right", off=(0.45, -0.3), z=0.9)
    scope.serve()
    try:
        if not await _on_stage(stack, viewer, out, scope):
            return out
        person = Person(viewer, scope)
        follow = asyncio.create_task(person.run(180))
        await viewer.send({"type": "ask", "text": "find me a stoma and centre it"})
        # The thing it found: its slide point, read from its outline as it was made (the slide had not moved yet).
        found = None
        deadline = time.monotonic() + 60
        while found is None and time.monotonic() < deadline and not follow.done():
            if person.target and person.target in viewer.scene:
                pts = [p for poly in viewer.scene[person.target]["polygons"] for p in poly]
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                found = scope.slide_point((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
            await asyncio.sleep(0.05)
        line = await follow
        out.observations.append({"lines": [x.get("say") for x in person.lines], "found": found})
        if not out.check("it found something and guided to it", found is not None, str(person.lines[:2])[:300]):
            return out
        stomata = [(x + stoma[0] - LEAF_STOMATA["right"][0], y + stoma[1] - LEAF_STOMATA["right"][1])
                   for x, y in LEAF_STOMATA.values()]
        near = min(math.hypot(found[0] - x, found[1] - y) for x, y in stomata)
        out.check(f"what it found is a labelled stoma ({near:.0f} slide px from one)", near <= 30)
        _truth(out, scope, found, line)
    finally:
        await viewer.send({"type": "tool", "name": "stop_guiding", "args": {}})
        await _put_back(stack, before)
        scope.close()
    return out
