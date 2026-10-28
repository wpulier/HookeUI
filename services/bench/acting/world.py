"""A stand-in world for the acting bench: the real brain, spoken-request handling and tools,
over a slide whose things sit at known places, a GPU that outlines what is at a box or a
point, scripted models, a magnifier and a voice that says "Done" when told something is done
(as GPT-Live did on 2026-10-04).

Times in a case are in real seconds; the world runs them `scale` times faster (the models'
latencies and brain/speech.py's join timings too), so a case of a few seconds takes a
fraction of one. Every time the user is told something (the chat's answer, the voice), it is
recorded with the board as it was at that moment (`claims`), and every change of the board is
recorded with when it happened (`boards`).

With `models="real"` (the live cases, kept privately: run.py) the camera is a real picture whose things are labelled
boxes, and the brain, the fast pass, the looks and the finder ask the real models (LiveAgent), in
real time. The outline model is still the stand-in: a box outlines the labelled thing it covers
best, or, covering none, itself (as EdgeTAM outlines whatever is there: a wall, 2026-10-10).
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import re
import tempfile
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from types import SimpleNamespace

from orchestrator.agent import Agent, _box_in
from orchestrator.board import Board
from orchestrator.brain.actions import outline_seen
from orchestrator.brain.loop import Brain, claims_done
from orchestrator.brain.speech import SpokenRequests
from orchestrator.brain.state import Conversation
from orchestrator.brain.toolbox import Services, build
from orchestrator.day.memory import Memory
from orchestrator.coords import Crop, frame_box_to_gemini
from orchestrator.day import DayStore
from orchestrator.options import Settings
from orchestrator.scene import Scene
from orchestrator.sight import Seen, SightView

WIDTH, HEIGHT = 1280, 720
FAST_S = 0.8  # the fast pass (gemini-3.8-flash on notes, fast bench p50)
VISION_S = 2.0  # one vision call with the picture
OUTLINE_S = 0.15  # one EdgeTAM outline
CELLS_S = 1.2  # a cell finder's run (Cellpose-SAM ~1-2 s at phase 0)
CAMERA_TICK_S = 0.05  # a live camera's frames, real seconds apart (20 fps), for cases whose `camera` is "live"
QUESTION = re.compile(r'User: "(.*)"\n\nFirst, what are', re.S)
FINAL_QUESTION = re.compile(r'User: "(.*?)"\nYour reply', re.S)
ANSWER_FACTS = re.compile(r'<answer-facts source="app" untrusted="true">\n(.*?)\n</answer-facts>', re.S)
SCRIPTED_VIEW = ContextVar("acting_scripted_view", default=None)
CHECK_PROMPT = "Does the magenta outline follow that thing's own edge?"
FAILED = re.compile(r"\b(could not|couldn't|didn't work|did not work|can't|cannot)\b", re.I)


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / union if union > 0 else 0.0


@dataclass
class Thing:
    """Something on the slide: where it is with the slide at rest (frame pixels) and whether
    the outline model can find its edge."""
    name: str
    box: tuple[float, float, float, float]
    outlinable: bool = True
    seen_as: str = ""  # what the running look calls it, when not its name (two stomata both "stoma with guard cells")


@dataclass
class Claim:
    at: float  # case seconds since the start
    text: str
    kind: str  # "done" | "failed" | "info"
    by: str  # "voice" | "chat" | "narrator"
    board: dict = field(default_factory=dict)
    about: str | None = None  # the request (its event's id) this was said about, when known


class Clock:
    def __init__(self, scale: float) -> None:
        self.scale, self.t0 = scale, time.monotonic()

    def now(self) -> float:
        """Case seconds since the start."""
        return (time.monotonic() - self.t0) / self.scale

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds) * self.scale)


TEXTURE_MARGIN = 1024  # how far the slide can move (px) and still be pictured from the same texture


def _texture(seed: int = 7):
    """The specimen as a picture: smooth random texture, so a patch of it matches only where it
    is (an outline's own pixels verify its place, ADR 0010)."""
    import cv2
    import numpy as np

    rng = np.random.default_rng(seed)
    noise = rng.integers(0, 255, (HEIGHT + 2 * TEXTURE_MARGIN, WIDTH + 2 * TEXTURE_MARGIN), dtype=np.uint8)
    return cv2.GaussianBlur(noise, (0, 0), 2.0)


class Slide:
    """The camera: frames with a motion offset and epoch; every frame stays available, with a
    picture of the specimen moved by the offset (another epoch is another part of the slide)."""

    texture = None

    def __init__(self, movers: list[dict] | None = None) -> None:
        self.picture: bytes | None = None  # a real camera picture (live cases): every frame shows it
        self.n = 1
        self.t0 = time.time()
        # Small dark things swimming over the specimen ({"from": [x, y], "velocity": [vx, vy]}: frame px, px per
        # second of real time), drawn on every frame where they are at its time (track_motion measures them).
        self.movers = list(movers or [])
        self.latest_info = SimpleNamespace(id=1, dx=0.0, dy=0.0, width=WIDTH, height=HEIGHT, epoch=0, fov=None,
                                           wall_at=time.time(), steady=True, focus=1.0)
        self.infos = {1: self.latest_info}
        self.jpegs: dict[int, bytes] = {}

    def move(self, dx: float = 0.0, dy: float = 0.0, epoch: int | None = None):
        self.n += 1
        info = SimpleNamespace(id=self.n, dx=self.latest_info.dx + dx, dy=self.latest_info.dy + dy, width=WIDTH,
                               height=HEIGHT, epoch=self.latest_info.epoch if epoch is None else epoch, fov=None,
                               wall_at=time.time(), steady=True, focus=1.0)
        self.infos[info.id] = info
        self.latest_info = info
        return info

    def tick(self):
        """A new frame of the same still view (the live camera keeps sending frames; enhance and track_motion
        need the last seconds of them)."""
        return self.move()

    def jpeg(self, frame_id: int) -> bytes:
        """The frame's picture: the texture moved by the frame's offset (or the real picture), with the movers
        where they are at the frame's time."""
        import cv2

        if self.picture is not None and not self.movers:
            return self.picture
        if frame_id not in self.jpegs:
            info = self.infos[frame_id]
            if self.picture is not None:
                import numpy as np

                view = cv2.imdecode(np.frombuffer(self.picture, np.uint8), cv2.IMREAD_COLOR)
            else:
                if Slide.texture is None:
                    Slide.texture = _texture()
                shift = 333 * int(info.epoch)  # another epoch: the slide is somewhere else
                x0 = int(round(TEXTURE_MARGIN - info.dx)) + shift
                y0 = int(round(TEXTURE_MARGIN - info.dy)) + shift
                x0 = max(0, min(Slide.texture.shape[1] - WIDTH, x0))
                y0 = max(0, min(Slide.texture.shape[0] - HEIGHT, y0))
                view = Slide.texture[y0:y0 + HEIGHT, x0:x0 + WIDTH]
                if self.movers:
                    view = view.copy()
            t = info.wall_at - self.t0
            for m in self.movers:
                x, y = m["from"][0] + m["velocity"][0] * t + info.dx, m["from"][1] + m["velocity"][1] * t + info.dy
                cv2.circle(view, (int(round(x)), int(round(y))), int(m.get("radius", 4)), (15, 15, 15) if view.ndim == 3 else 15, -1)
            self.jpegs[frame_id] = cv2.imencode(".jpg", view, [int(cv2.IMWRITE_JPEG_QUALITY), 90])[1].tobytes()
        return self.jpegs[frame_id]


class Hub:
    """The orchestrator's hub as the brain and its tools use it."""

    def __init__(self, world: "World") -> None:
        self.world = world
        self.slide = Slide(world.case.get("movers"))
        self.scene = Scene()
        self.settings = Settings()
        self.settings.fast = "gemini-3.8-flash"
        self.gpu = FakeGPU(world)
        self.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=self.slide.jpeg(fid))
                                      if fid in self.slide.infos else None, is_fresh=lambda: True)
        self.clients, self.sent, self.focused = set(), [], None

    @property
    def latest_info(self):
        return self.slide.latest_info

    @property
    def infos(self):
        return self.slide.infos

    def best_frame(self, window_s: float = 1.0):
        return self.slide.latest_info  # the stand-in slide: the newest frame is the best one

    def gray(self, _frame_id):
        return None

    def candidates_in(self, _frame_id):
        return []

    def map_point(self, x, y, frame_id, to_id):
        a, b = self.infos.get(frame_id), self.infos.get(to_id)
        if a is None or b is None or a.epoch != b.epoch:
            return None
        return x + b.dx - a.dx, y + b.dy - a.dy

    def broadcast(self, payload) -> None:
        self.sent.append(payload)
        studio = getattr(self, "studio", None)
        if studio is not None:
            studio.hear(payload)


class Studio:
    """The studio as the screen report sees it (orchestrator/screen.py): it applies what it is sent and
    reports what it shows, so a change on screen is done only once that report says so."""

    def __init__(self, hub) -> None:
        self.hub = hub
        self.items: dict[str, dict] = {"live": {"id": "live", "kind": "live", "visible": True, "opacity": 1.0, "z": 0},
                                       "outlines": {"id": "outlines", "kind": "outlines", "visible": True, "opacity": 1.0, "z": 2}}
        self.applied: list[str] = []
        self.picture_labels: list[str] = []

    def hear(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "layer":
            for key in [k for k in self.items if k.startswith("layer:")]:
                del self.items[key]
            if message.get("population"):
                key = f"layer:{message['population']}"
                style = dict(message.get("style") or {})
                self.items[key] = {"id": key, "kind": "population", "visible": True, "opacity": style.pop("opacity", 1.0),
                                   "style": {k: v for k, v in style.items() if k not in ("values", "legend")}, "z": 1}
        elif kind == "present":
            item = self.items.setdefault(message["id"], {"id": message["id"], "kind": message["kind"]})
            for k, v in message["set"].items():
                if k in ("opacity", "visible", "open", "rect"):
                    item[k] = v
                elif k == "front":
                    item["z"] = 99
                else:  # a style the studio draws it with (colour, fill, emphasis...), as tests/test_screen's studio
                    item.setdefault("style", {})[k] = v
            self.applied.append(message["change"])
        elif kind == "magnifier":
            for source in message.get("sources", []):
                label = source.get("label")
                if label and label not in self.picture_labels:
                    self.picture_labels.append(label)
        elif kind == "presenter":  # a researcher's work beside the view, as the studio's result viewer shows it
            self.items["viewer"] = {"id": "viewer", "kind": "viewer", "open": bool(message.get("open")),
                                    "shows": message.get("rid"), "z": 11}
        elif kind != "screen-ask":  # a wait asks for a report now
            return
        try:
            asyncio.get_running_loop().call_soon(self.report)
        except RuntimeError:
            self.report()

    def report(self) -> None:
        screen = getattr(self.hub, "screen", None)
        magnifier = getattr(self.hub.world, "magnifier", None)
        if magnifier is not None:  # the stand-in magnifier's state, as the studio would draw it
            beside = magnifier.shown_beside() if hasattr(magnifier, "shown_beside") else None
            self.items["magnifier"] = {"id": "magnifier", "kind": "magnifier", "open": bool(magnifier.open),
                                       "shows": magnifier.displayed_object, "source": ((magnifier.picture_now() or {}).get("url") or
                                           (magnifier.sources[magnifier.index].get("kind") if magnifier.sources else None)) if magnifier.open else None,
                                       "pictures": [{"source": s["url"], "scale": s.get("displayScale", 1)}
                                                    for s in [magnifier.picture_now(), beside] if s and s.get("kind") == "image"],
                                       "beside": beside["url"] if beside else None,
                                       "level": (1.0 if magnifier.picture_now() else magnifier.factor) if magnifier.open else None,
                                       "z": 5}
        if screen is not None:
            screen.report({"items": list(self.items.values()), "applied": list(self.applied)})

    def changed(self) -> None:
        """Something the studio draws changed on its own state (the magnifier): reported as it draws it."""
        try:
            asyncio.get_running_loop().call_soon(self.report)
        except RuntimeError:
            self.report()


class FakeGPU:
    """Outlines what is on the slide: inside a box, or the thing under a point. A thing the
    outline model cannot find the edge of gives nothing (as large tissue did on 2026-10-04)."""

    ready = True

    def __init__(self, world: "World") -> None:
        self.world = world
        self.loaded: set[str] = set()  # models used once: a case's `cold` model loads on its first use only
        self.models: list[str] = []  # every model asked for, in order (what really ran)

    name = "main"  # the worker's name, as loads.py says where a model loads
    loading: dict = {}

    async def load(self, model: str | None) -> None:
        """A case's `cold` model ({"micro-sam-lm": 8}: seconds) takes that long the first time it is used, as
        micro-sam's first outline did live (2026-10-11 02:37, 120 s), and says so as a worker does: a load message
        when it begins and when it has loaded (gpu_worker/loading.py), which the app's Loads hears (loads.py)."""
        if model:
            self.models.append(model)
        cold = (self.world.case.get("cold") or {}).get(model or "")
        if cold and model not in self.loaded:
            self.loaded.add(model)
            loads = getattr(self.world.hub, "loads", None)
            if loads is not None:
                loads.told(self, {"load": "loading", "model": model})
            await self.world.clock.sleep(float(cold))
            if loads is not None:
                loads.told(self, {"load": "loaded", "model": model, "seconds": float(cold)})

    async def call(self, op: str, timeout: float = 30.0, **payload) -> dict:
        await self.load(payload.get("model"))
        if op in ("cells", "automask"):
            return await self.cells(op, payload)
        if op == "embed":
            return self.embed(payload)
        assert op == "segment", op
        await self.world.clock.sleep(OUTLINE_S)
        info = self.world.hub.infos[payload["frame_id"]]
        if self.world.real:
            return self.real_segment(payload, info)
        if "box" in payload:
            x0, y0, x1, y1 = payload["box"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        else:
            (cx, cy), = payload["points"]
        thing = self.world.thing_at(cx, cy, info)
        if thing is None or not thing.outlinable:
            return {"polygons": [], "score": 0.0, "ms": OUTLINE_S * 1000}
        x0, y0, x1, y1 = self.world.frame_box(thing, info)
        return {"polygons": [[[x0 + 2, y0 + 2], [x1 - 2, y0 + 2], [x1 - 2, y1 - 2], [x0 + 2, y1 - 2]]], "score": 0.9,
                "ms": OUTLINE_S * 1000}

    def real_segment(self, payload: dict, info) -> dict:
        """On a real picture: a box outlines the labelled thing it overlaps best (IoU at least 0.3), a point the
        smallest labelled thing under it; anything else is outlined as itself (a box, or 48 px round a point),
        never refused: the real outline model outlines whatever is there."""
        if "box" in payload:
            box = tuple(float(v) for v in payload["box"])
            best = max(self.world.things, key=lambda t: iou(box, self.world.frame_box(t, info)), default=None)
            if best is not None and iou(box, self.world.frame_box(best, info)) >= 0.3:
                box = self.world.frame_box(best, info)
        else:
            (cx, cy), = payload["points"]
            thing = self.world.thing_at(cx, cy, info)
            box = self.world.frame_box(thing, info) if thing else (cx - 24, cy - 24, cx + 24, cy + 24)
        x0, y0, x1, y1 = box
        return {"polygons": [[[x0 + 2, y0 + 2], [x1 - 2, y0 + 2], [x1 - 2, y1 - 2], [x0 + 2, y1 - 2]]], "score": 0.9,
                "ms": OUTLINE_S * 1000}


    def embed(self, payload: dict) -> dict:
        """A stand-in picture model: each crop's colour statistics as a unit vector (deterministic; groups by it
        are arbitrary on this texture, so cases judge the words and the steps, not which cells group)."""
        import cv2
        import numpy as np

        out = []
        for jpeg in payload["jpegs"]:
            crop = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR).astype(np.float32)
            v = np.concatenate([crop.mean((0, 1)), crop.std((0, 1))])
            out.append((v / max(float(np.linalg.norm(v)), 1e-9)).tolist())
        return {"vectors": out, "model": payload.get("model")}

    async def cells(self, op: str, payload: dict) -> dict:
        """The cell finder (a label map, as Cellpose gives) or EdgeTAM's automatic masks (one mask
        each): every thing on the slide whose name has "cell" in it, as an ellipse filling its box (a
        90 x 30 box is a cell three times longer than wide)."""
        import cv2
        import numpy as np

        from gpu_worker.worker import encode_labels

        await self.world.clock.sleep(CELLS_S)
        info = self.world.hub.infos[payload["frame_id"]]
        cells = [t for t in self.world.things if "cell" in t.name.lower()]
        if op == "cells":
            labels = np.zeros((HEIGHT, WIDTH), np.uint16)
            for n, t in enumerate(cells, start=1):
                x0, y0, x1, y1 = self.world.frame_box(t, info)
                cv2.ellipse(labels, (int((x0 + x1) / 2), int((y0 + y1) / 2)), (int((x1 - x0) / 2), int((y1 - y0) / 2)),
                            0, 0, 360, n, -1)
            return {"labels": encode_labels(labels), "scores": {str(n): 0.9 for n in range(1, len(cells) + 1)},
                    "score": "cellprob", "polygons": [], "count": len(cells), "ms": CELLS_S * 1000}
        regions = []
        for t in cells:
            x0, y0, x1, y1 = (int(round(v)) for v in self.world.frame_box(t, info))
            bits = np.zeros((y1 - y0 + 1, x1 - x0 + 1), np.uint8)
            cv2.ellipse(bits, ((x1 - x0) // 2, (y1 - y0) // 2), ((x1 - x0) // 2, (y1 - y0) // 2), 0, 0, 360, 1, -1)
            regions.append({"polygons": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]], "score": 0.9, "stability": 0.95,
                            "mask": {"bits": np.packbits(bits.astype(bool)).tobytes(), "shape": list(bits.shape),
                                     "x0": float(x0), "y0": float(y0), "scale": 1.0}})
        return {"regions": regions, "prompts": 64, "candidates": 0, "ms": CELLS_S * 1000}


from orchestrator.magnifier import Magnifier


class ScriptedModel:
    """External canned provider, entered through the real Agent/admission boundary.

    Outcome replies are explicit case inputs, never generated from a score or board.
    An unscripted interpretation times out; production must not invent its answer.
    """

    def __init__(self, world: "World", rules: list[dict]) -> None:
        self.world, self.rules = world, rules
        self.prompts: list[str] = []
        self.sends: list = []  # passive actual provider-entry facts, including failed sends

    def outcome(self, question, facts, prompt, request_id):
        script = (match(self.world.agent.vision_rules, question, {})
                  or match(self.rules, question, {}))  # the same case's fast-only original plan
        reply = script.get("final") or script.get("then")
        if reply is None:
            raise asyncio.TimeoutError("no scripted outcome reply for this request")
        requirements = reply.get("requires", {})
        for text in requirements.get("limitations", []):
            if not any(text in item for item in facts.get("limitations", [])):
                raise asyncio.TimeoutError("the scripted outcome's supplied limitation is absent")
        problems = prompt.partition("What did not work:\n")[2].partition("What did work:\n")[0]
        if any(text not in problems for text in requirements.get("problems", [])):
            raise asyncio.TimeoutError("the scripted outcome's supplied problem is absent")
        for required in requirements.get("operations", []):
            rows = [row for row in facts["operations"]
                    if row.get("tool") == required["tool"] and row.get("ok") is required["ok"]
                    and row.get("requestId") == request_id and row.get("actionId")
                    and all(text in str(row.get("summary") or "")
                            for text in required.get("summary_contains", []))]
            if len(rows) != 1:
                raise asyncio.TimeoutError("the scripted outcome has no unique original operation receipt")
        # Existing `then` inputs belong to completed successful queries of this
        # original plan, not an arbitrary later request or the world's newest work.
        if "final" not in script:
            query_tools = {action["tool"] for action in script.get("actions", [])
                           if self.world.brain.registry.kind(action["tool"]) == "query"}
            if not query_tools or not all(any(row.get("tool") == tool and row.get("ok") is True
                                             for row in facts["operations"]) for tool in query_tools):
                raise asyncio.TimeoutError("the scripted query outcome has no successful supplied receipt")
        reply = {key: value for key, value in reply.items() if key not in ("requires", "confirmation")}
        if "{n}" in str(reply.get("answer", "")):
            # This is an external scripted model reading supplied receipt text,
            # not production numeric authority or a fabricated typed measurement.
            numbers = []
            for row in facts["operations"]:
                if row.get("ok") is not True:
                    continue
                summary = row.get("summary", "")
                if row.get("tool") == "count":
                    found = re.match(r"(\d+)\b", summary)
                elif row.get("tool") == "query_population":
                    found = re.search(r": (\d+) instances\b", summary)
                elif row.get("tool") == "resources":
                    found = re.search(r"Cell outlines and matches: \d+ \(newest: [^)]*\(e\d+\), (\d+) instances", summary)
                else:
                    continue
                if found:
                    numbers.append(found.group(1))
            if len(numbers) != 1:
                raise asyncio.TimeoutError("the scripted quantity has no unique supplied receipt")
            reply = {**reply, "answer": reply["answer"].replace("{n}", numbers[0])}
        return reply

    async def ask(self, images, prompt, max_output_tokens=0):
        from orchestrator.brain.dispatch import in_answer_phase
        from orchestrator.ticket import current

        self.prompts.append(prompt)
        request = current()
        attempt = request.admission.attempts[-1] if in_answer_phase() and request is not None else None
        block = ANSWER_FACTS.search(prompt)
        facts = json.loads(block.group(1)) if block else None
        sent = SimpleNamespace(ticket=request, attempt=attempt, images=tuple(images), prompt=prompt,
                               facts=facts, reply=None)
        self.sends.append(sent)
        if CHECK_PROMPT in prompt:  # the outline check (outline_check.py): the case's `check` rules
            await self.world.clock.sleep(FAST_S)
            return SimpleNamespace(data=self.world.check_reply(images), latency_ms=FAST_S * 1000, error=None, model="fake")
        held = SCRIPTED_VIEW.get()
        m = QUESTION.search(prompt) or FINAL_QUESTION.search(prompt)
        question = held[0] if held is not None else m.group(1) if m else None
        latency = VISION_S if held is not None else FAST_S
        await self.world.clock.sleep(latency)
        if facts is not None and attempt is not None and attempt.phase in ("query", "final", "late"):
            if question is None:
                question = (prompt.partition("Original request and completed receipts:\n")[2]
                            or prompt.partition("User request: ")[2]).partition("\n")[0]
            # A tool-capable completion round carries receipts after the original
            # words. Match the fixture's question, then validate those real receipts.
            question = question.split("\n\n(Results of every step already attempted", 1)[0]
            reply = self.outcome(question, facts, prompt, request.admission.request_id)
        elif held is not None:
            reply = match(self.world.agent.vision_rules, question,
                          {"answer": "That's part of the specimen.", "targets": []})
        elif m is not None:
            reply = match(self.rules, question, {"intent": "question", "needs_look": True})
        else:
            raise asyncio.TimeoutError("no scripted reply for this prompt")
        reply = self.world.fill({key: value for key, value in reply.items() if key not in ("then", "final")})
        targets = []
        for target in reply.get("targets") or []:
            if held is None:
                raise ValueError("a scripted camera target requires the actual held look")
            info, _image, crop = held[1]
            thing = self.world.thing(target.get("thing") or target.get("label"))
            box = frame_box_to_gemini(self.world.frame_box(thing, info), crop) if thing else [0, 0, 1, 1]
            targets.append({"label": target.get("label") or (thing.name if thing else ""), "box_2d": box,
                            "confidence": target.get("confidence", 0.8)})
        if "targets" in reply:
            reply = {**reply, "targets": targets}
        sent.reply = reply
        return SimpleNamespace(data=reply, latency_ms=latency * 1000, error=None,
                               model=attempt.model if attempt is not None else "explicitly-scripted-provider")


class ScriptedAgent(Agent):
    """Real Agent look/snapshot/pointing over synthetic camera pixels and provider replies."""

    def __init__(self, world: "World", conversation: Conversation, vision_rules: list[dict], fast: ScriptedModel) -> None:
        super().__init__(world.hub, conversation)
        self.world, self.vision_rules, self.fast = world, vision_rules, fast
        self.logged: list[dict] = []
        self.looks: list[str] = []
        self.tables: list[str] = []  # the scene table each look was given (what the brain was told)

    def available(self, option_id=None) -> bool:
        return True

    def vision(self, option_id):
        return self.fast

    def log(self, record: dict) -> None:
        self.logged.append({"t": round(self.world.clock.now(), 3), **record})

    def keep_view(self, *a, **k):
        return None

    async def look(self, question, view, *, option, pointing="box", notes=None, mode="json-actions", registry=None):
        self.looks.append(question)
        self.tables.append(notes[1] if notes else "")
        # Bind only this external script's original view; no latest-frame lookup
        # and no copied admission/Agent implementation. Real Agent encodes pixels.
        token = SCRIPTED_VIEW.set((question, view))
        try:
            return await super().look(question, view, option=option, pointing=pointing, notes=notes,
                                      mode=mode, registry=registry)
        finally:
            SCRIPTED_VIEW.reset(token)


    async def look_in(self, prompt, view, region=None, option=None):
        """The finder's look (Agent.look_in): the case's `find` rules say which things the model
        boxes; it boxes them where they are, inside `region` when given."""
        await self.world.clock.sleep(VISION_S)
        info = view[0]
        rules = [{"when": r["when"], "reply": r} for r in self.world.case.get("find", [])]
        rule = match(rules, prompt, {"things": [], "note": "nothing like that here"})
        targets = []
        for name in rule.get("things", []):
            for thing in [t for t in self.world.things if name.lower() in t.name.lower()]:
                box = self.world.frame_box(thing, info)
                cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                if region is None or (region[0] <= cx <= region[2] and region[1] <= cy <= region[3]):
                    targets.append({"box": box, "label": thing.name, "confidence": 0.8})
        return {"targets": targets, "note": rule.get("note", "")}, {"option": option or "fake", "model": "fake",
                                                                     "latency_ms": VISION_S * 1000}


class Recorded:
    """A real provider, with every call it is asked kept for the raw results (live cases)."""

    def __init__(self, world: "World", option: str, inner) -> None:
        self.world, self.option, self.inner = world, option, inner

    async def ask(self, images, prompt, max_output_tokens=1500):
        from orchestrator.brain.dispatch import in_answer_phase
        from orchestrator.ticket import current

        request = current()
        attempt = request.admission.attempts[-1] if in_answer_phase() and request is not None else None
        started = self.world.clock.now()
        result = await self.inner.ask(images, prompt, max_output_tokens=max_output_tokens)
        self.world.calls.append({"t": round(started, 3), "option": self.option, "phase": getattr(attempt, "phase", None),
                                 "images": len(images), "promptChars": len(prompt),
                                 "prompt": hashlib.sha256(prompt.encode()).hexdigest()[:16],
                                 "ms": round(result.latency_ms), "error": result.error, "data": result.data})
        self.world.prompts.setdefault(hashlib.sha256(prompt.encode()).hexdigest()[:16], prompt)
        return result


class LiveAgent(Agent):
    """The real Agent asking the real models (live cases): the looks, the fast pass, the finder and the answer
    rounds go to the providers. What it logs is kept here (never in the app's logs); no picture is kept."""

    def __init__(self, world: "World", conversation: Conversation) -> None:
        super().__init__(world.hub, conversation, interaction_log=world.root / "interactions.jsonl")
        self.world = world
        self.vision_rules: list[dict] = []
        self.logged: list[dict] = []
        self.looks: list[str] = []
        self.tables: list[str] = []

    def vision(self, option_id):
        if option_id not in self._visions:
            self._visions[option_id] = Recorded(self.world, option_id, super().vision(option_id))
        return self._visions[option_id]

    def log(self, record: dict) -> None:
        self.logged.append({"t": round(self.world.clock.now(), 3), **record})

    def keep_image(self, *a, **k):
        return None

    def keep_view(self, *a, **k):
        return None

    async def look(self, question, view, *, option, pointing="box", notes=None, mode="json-actions", registry=None):
        self.looks.append(question)
        self.tables.append(notes[1] if notes else "")
        return await super().look(question, view, option=option, pointing=pointing, notes=notes, mode=mode,
                                  registry=registry)


class Voice:
    """GPT-Live as the bench sees it: told things silently, and what it then says. Told that
    something is already done, it says "Done" (2026-10-04 14:06:56, 14:09:55)."""

    def __init__(self, world: "World") -> None:
        self.world = world
        self.closed = False
        self.told: list[tuple[float, str]] = []

    async def inject(self, text: str, respond: bool = False, **_log) -> None:
        self.told.append((self.world.clock.now(), text))
        if "already done" in text or "has already" in text:
            self.world.claim("Done.", "done", "voice")
        elif "could not" in text:
            self.world.claim("That didn't work, I'm looking again.", "failed", "voice")
        elif "still working on" in text:
            self.world.claim("On it.", "info", "voice")


class Narrator:
    """What the app says at the next pause, said at once."""

    def __init__(self, world: "World") -> None:
        self.world = world

    def offer(self, line) -> None:
        self.world.claim(line.text, kind_of(line.text), "narrator")


RESEARCH_S = 3.0  # how long a stand-in researcher works (case seconds)


class ResearchWorker:
    """The research process as the bench sees it (research/manager.py's worker channel), never a model: each
    researcher started works RESEARCH_S, does what the case's first matching `researcher` rule says for its brief
    (`run`: one of the app's operations through its hands, as research_worker's `run` tool asks for it), then
    finishes with a short written finding, and with a lesson of two steps when a lesson was asked for."""

    alive = True
    runs_a_model = False  # never a model: starting one needs no model key (research/manager.engine_key_refusal)

    def __init__(self, world: "World") -> None:
        self.world = world
        self.manager = None
        self.jobs: list[dict] = []
        self.pending: list[asyncio.Future] = []
        self.ran: list[dict] = []  # what each `run` the app was asked for replied

    async def call(self, operation: str, **job) -> dict:
        if operation == "cancel":
            self.manager._event({"rid": job["rid"], "event": "done", "status": "cancelled"})
            return {}
        if operation != "spawn":
            return {}
        self.jobs.append(job)
        self.manager._event({"rid": job["rid"], "event": "status", "status": "running"})
        self.pending.append(asyncio.ensure_future(self.work(job)))
        return {}

    async def work(self, job: dict) -> None:
        from research_worker.results import lesson_content

        rule = match(self.world.case.get("researcher", []), job.get("brief") or "", {})
        await self.world.clock.sleep(RESEARCH_S)
        if rule.get("run"):
            self.ran.append(await self.manager._request({"op": "run", "rid": job["rid"], "why": "its brief asks for it",
                                                         **rule["run"]}))
        title = rule.get("title") or "Stomata"
        summary = rule.get("summary") or "Stomata are pores in the leaf's surface; two guard cells open and close each one."
        if job.get("deliverable") == "lesson":
            content = lesson_content(title, ["two guard cells surround each pore"],
                                     [{"question": "What do guard cells control?", "answer": "the opening of the pore",
                                       "hint": "think gas exchange"},
                                      {"question": "Where are stomata most often found?", "answer": "the underside of a leaf"}])
            folder = Path(job["dir"]) / "outputs"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "lesson.html").write_text(f"<html><body><h1>{title}</h1></body></html>")
            self.manager._event({"rid": job["rid"], "event": "output", "kind": "html", "path": "outputs/lesson.html",
                                 "caption": "Interactive lesson", "content": content})
        self.manager._event({"rid": job["rid"], "event": "done", "status": "done", "spent": 0.0,
                             "result": {"title": title, "summary": summary, "one_liner": summary}})

    def stop(self) -> None:
        self.alive = False
        for task in self.pending:
            task.cancel()


def kind_of(text: str) -> str:
    if FAILED.search(text or ""):
        return "failed"
    if (text or "").startswith("Done") or claims_done(text or ""):
        return "done"
    return "info"


def match(rules: list[dict], words: str, default: dict) -> dict:
    said = (words or "").lower()
    for rule in rules:
        if all(w.lower() in said for w in ([rule["when"]] if isinstance(rule["when"], str) else rule["when"])):
            return rule["reply"]
    return default


def load_picture(name: str) -> bytes:
    """A live case's camera picture, as a 1280x720 JPEG: a path, or a name in the assets store's bench folder."""
    import cv2

    from orchestrator.assets import store

    path = Path(name).expanduser()
    if not path.is_absolute():
        path = store().root / "bench" / name
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"the live case's picture is not in the assets store: {path}")
    image = cv2.resize(image, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 92])[1].tobytes()


class _Downloads:
    """The models folder's queue, recorded (downloads.Downloads.start): what was started, never fetched."""

    def __init__(self, started: list) -> None:
        self.started = started

    def start(self, model: str, by: str = "you") -> dict:
        self.started.append(model)
        return {"model": model, "state": "queued", "total": 3_500_000_000}


class World:
    def __init__(self, case: dict, scale: float = 0.1, models: str = "scripted", day_clock=None) -> None:
        self.case = case
        self.not_downloaded = (case.get("setup") or {}).get("not_downloaded", [])  # models this case's Mac lacks
        self.downloads: list[str] = []  # the models the agent started downloading (get_model)
        self.pages: list[str] = []  # the pages the agent opened in the studio (open_models)
        self.real = models == "real"
        self.clock = Clock(scale)
        self.things = [Thing(t["name"], tuple(t["box"]), t.get("outlinable", True), t.get("seen_as", ""))
                       for t in case.get("slide", [])]
        self.calls: list[dict] = []  # live cases: every provider call (Recorded)
        self.prompts: dict[str, str] = {}
        self.hub = Hub(self)
        if case.get("picture"):
            self.hub.slide.picture = load_picture(case["picture"])
        if self.real:
            self.hub.settings.fast = Settings().fast  # the app's defaults, changed only by the run's settings
            problems = self.hub.settings.update(case.get("settings", {}))
            if problems:
                raise ValueError(f"{case['id']}: {'; '.join(problems)}")
        from orchestrator.screen import Screen

        self.hub.screen = Screen(self.hub.broadcast)  # what the studio reports it shows (screen.py)
        self.hub.studio = Studio(self.hub)
        self.hub.studio.report()
        self.claims: list[Claim] = []
        self.boards: list[tuple[float, dict]] = []
        self.request_turns: dict[str, int] = {}
        self.snapshot_runs: list[tuple] = []
        self._snapshot_proofs: dict[tuple, dict] = {}
        self.magnifier = Magnifier(self.hub)
        root = self.root = Path(tempfile.mkdtemp(prefix="micromind-acting-"))
        # The session store's clock: the dates its sessions and journal fall on (`day_clock` pins the time of day).
        self.conversation = Conversation(store=DayStore(root=root, clock=day_clock or time.time))
        self.fast = ScriptedModel(self, case.get("fast", []))
        self.agent = (LiveAgent(self, self.conversation) if self.real else
                      ScriptedAgent(self, self.conversation, case.get("vision", []), self.fast))
        self.sight = SimpleNamespace(view=None)
        # Researchers, as the app wires them (app.py), over a stand-in research process (never a model).
        from orchestrator.presenter import Presenter
        from orchestrator.research import ResearchManager
        from orchestrator.research.desk import Desk

        day = self.conversation.store
        self.first_session, self.resumed = day.sid, None  # the session the case began in; the one a restart opens
        self.research = ResearchManager(self.hub, self.conversation, root=root / "research", today=lambda: day.date)
        self.research_worker = ResearchWorker(self)
        self.research_worker.manager = self.research
        self.research._worker = self.research_worker  # only the process channel is the stand-in
        self.research.log = lambda record: self.agent.log(record)
        self.presenter = Presenter(self.hub, self.research, outcomes=day.outcomes_of, prefs=(day.prefs.get, day.set_pref))
        self.services = Services(hub=self.hub, agent=self.agent, magnifier=self.magnifier, sight=self.sight,
                                 outline_seen=partial(outline_seen, self.hub, lambda: self.sight.view),
                                 segment_all=self.segment_all, snapshots=root / "snapshots",  # never the real folder
                                 find_reference=self.find_reference,  # never the web
                                 research=self.research, presenter=self.presenter, day=day,
                                 # recall, as the app has it (day/memory.py), with no model: nothing is summarised
                                 memory=Memory(day, self.conversation, ask=None, research=self.research),
                                 desk=Desk(self.research, shown=lambda: self.presenter.rid if self.presenter.open else None),
                                 capabilities=self.capabilities_wire,  # the agent reads what the Capabilities page shows
                                 # Awareness reads the rows alone, as in the app (App.capability_status): without it every
                                 # request here built the whole Models page (~90 ms each), so the bench's timings drifted.
                                 capability_status=self.capability_status,
                                 # The Models page's queue and card (brain/toolbox/catalog.py): stand-ins that record
                                 downloads=_Downloads(self.downloads), open_page=self._open_page,
                                 # set_default changes settings as the Models menu does, kept for the day (defaults.py)
                                 change_settings=lambda changes, by: self._change_settings(changes, by))
        self.brain = Brain(self.hub, self.agent, build(self.services), self.conversation)
        # The session's index, as the app wires it (resources.py): what tools make becomes result entries.
        from orchestrator.resources import Index, Results

        results = Results(root / "results", day=self.conversation.store,
                          pictures=lambda key: self.magnifier.pictures.get(key))
        self.hub.resources = Index(self.hub, results=results, registry=self.brain.registry, research=self.research,
                                   snapshots=root / "snapshots", day=self.conversation.store)
        self.brain.registry.retaining.append(lambda run, ctx: results.finish_run(run, ctx, self.hub))
        # Every tool run's own words (a query's result goes to the model, not the user): `expect.queried` checks them.
        self.ran: list[tuple[str, bool, str]] = []
        self.brain.registry.finished.append(lambda run, ctx: self.ran.append((run.tool, bool(run.ok), str(run.summary or ""))))
        self.brain.registry.finished.append(
            lambda run, ctx: self.snapshot_runs.append((run, ctx)) if run.tool == "snapshot" and run.ok else None)
        self.brain.services, self.brain.sight, self.brain.narrator = self.services, self.sight, Narrator(self)
        self.brain.board = self.services.board = Board(self.hub, self.magnifier, None, self.sight)
        self.voice = Voice(self)
        self.brain.voice = self.voice
        from orchestrator.outcomes import Outcomes

        self.hub.outcomes = Outcomes([], None, None, hub=self.hub, more=lambda: list(self.agent.logged))
        from orchestrator.operations import runner

        runner(self.hub).log = self.agent.log  # what ran is recorded in the interaction log, as the app wires it (app.py)
        self.brain.report_late = self._late
        # Models loading (loads.py), as the app wires them: said on the request's step and as an Activity line
        # (activity.py), measured into this case's own folder. The bench's Activity counts a model's line as told
        # (a claim by "activity"), as the studio's Activity panel shows it.
        from orchestrator.activity import Activity
        from orchestrator.loads import Loads

        world = self

        class SaidActivity(Activity):
            def __init__(self) -> None:  # only what model loads and requests read; no journal, no studio
                self.models, self.dialogue, self.clock = {}, None, world.clock.now

            def add(self, kind, text, **facts):
                if kind == "model":
                    world.claim(str(text), "info", "activity")
                return {"kind": kind, "text": text, "state": facts.get("state", "done"), "id": facts.get("id")}

        self.hub.activities = SaidActivity()
        self.hub.loads = Loads(root / "models" / "loads.json", log=self.agent.log, hub=self.hub,
                               free_mb=lambda: 64_000.0)
        self.hub.loads.register(self.hub.gpu)
        self.speech = SpokenRequests(self.brain, None, self.voice, clock=self.clock.now)
        if case.get("check") is not None:
            # The app's outlines checked after they are drawn (outline_check.py, option OUTLINE_CHECK "fix"), as the
            # app wires it; a repair that moved one is said where the request was made (app.said_late).
            from orchestrator.outline_check import OutlineCheck

            async def outline_again(capture, box, label, made_by=None):
                from orchestrator import tools

                r = await tools.segment(self.hub, None, capture.id, capture.jpeg, (box[0] + box[2]) / 2,
                                        (box[1] + box[3]) / 2, box=box, label=label, capture=capture, origin="background")
                return {"polygons": r.get("polygons"), "madeBy": r.get("madeBy")}

            self.checker = OutlineCheck(self.hub, self.agent, outline_again, log=self.agent.log,
                                        tell=lambda request, text: self.claim(text, kind_of(text), "narrator"))
        self._setup(case.get("board", {}))

    def _change_settings(self, changes: dict, by: str) -> list[str]:
        """Settings changed from now on (commands.change_settings without a studio): applied and the day's model
        choices kept (defaults.change), so a restart restores them."""
        from orchestrator import defaults

        return defaults.change(self.hub, self.conversation.store, changes, by)

    async def _open_page(self, client, page: str) -> dict:
        """The studio opening a page for the agent (App.open_page): it shows it at once, in view."""
        self.pages.append(page)
        return {"page": page, "shown": True, "visible": True}

    def capabilities_wire(self) -> dict:
        """The capability list as the app's Capabilities page and the agent's `capabilities` tool read it
        (App.capabilities_wire), with the keys this process has (never read from the keys file here)."""
        from orchestrator import readiness

        with readiness.batch():  # the same response boundary as App.capabilities_wire
            return self._capabilities_wire()

    def capability_status(self) -> dict:
        """What the agent's awareness reads, as App.capability_status: the same resolver's capability rows, fresh, with
        none of the Models page's rows."""
        import os

        from orchestrator import capabilities, readiness
        from orchestrator.keys import KNOWN_KEYS

        keys = {name: bool(os.environ.get(name)) for name in KNOWN_KEYS}
        with readiness.batch():
            return {"capabilities": capabilities.wire(self.hub.settings, keys, self.brain.registry)["capabilities"]}

    def _capabilities_wire(self) -> dict:
        import os

        from orchestrator import capabilities
        from orchestrator.keys import KNOWN_KEYS

        keys = {name: bool(os.environ.get(name)) for name in KNOWN_KEYS}
        wire = capabilities.wire(self.hub.settings, keys, self.brain.registry)
        # The Models page's rows, as App.capabilities_wire adds them; a case's `setup.not_downloaded` models are as on a
        # Mac that has not downloaded them (every other model counts as downloaded, as in the tests).
        from orchestrator import catalog_view

        from gpu_worker.manifests import services
        from orchestrator.key_admin import _short

        # Each service's key as KeyAdmin.facts says it, present as this process's environment has it (never a value).
        wire["setup"]["keys"] = {sv.key: {"service": sv.id, "name": sv.name, "short": _short(sv.name), "getKey": sv.get_key,
                                          "enables": sv.enables, "present": keys.get(sv.key, False),
                                          "source": "environment" if keys.get(sv.key) else None,
                                          "sourceSays": "set outside the app" if keys.get(sv.key) else "", "canSave": False}
                                 for sv in services().values() if sv.key}
        rows = catalog_view.rows(wire["roles"], wire["setup"]["keys"], keys, [])
        for row in rows:
            if row["id"] in self.not_downloaded and row["runs"] != "cloud":
                says = f"Download {catalog_view.readiness.size_words(row['size'] or 1)}"
                row.update(status="needs-setup", word=says, group="needs-you" if row["chosen"] else "available",
                           next={"kind": "download", "says": says, "model": row["id"]})
        wire["models"] = rows
        return wire

    async def _late(self, ctx, run) -> None:
        """A tool that finished after its answer, told as announcer.Announcer.late_tool tells it: by voice
        "Done: ..." at the next pause; in the chat its action line updates (a step's report, not a claim
        that the request is done)."""
        if ctx.channel == "voice" and self.real:
            # As announcer.late_tool does now: the voice is given the run's own model answer, or its facts as data
            # (no "Done:" of the app's making); only an answer is said as one.
            if run.answer:
                self.claim(run.answer, kind_of(run.answer), "voice")
            else:
                self.claim(run.summary, "info" if run.ok else "failed", "voice")
        elif ctx.channel == "voice":
            said = f"Done: {run.summary}" if run.ok else f"Couldn't do it: {run.summary}"
            self.claim(said, kind_of(said), "voice")
        else:
            self.claim(run.summary, "info" if run.ok else "failed", "chat-action")

    async def segment_all(self, hub, view, box, text=""):
        """SAM 3.1's every-match (tools.segment_all): every thing of the same kind as the one in
        `box` (names that differ only by a trailing number: "red cell 1", "red cell 2"), on `view`
        (the request's held capture)."""
        await self.clock.sleep(1.5)
        info = hub.infos[view] if isinstance(view, int) else view
        example = self.thing_at((box[0] + box[2]) / 2, (box[1] + box[3]) / 2, info) if box else None
        kind = re.sub(r"\s*\d+$", "", example.name if example else text).lower()
        out = []
        for t in self.things:
            if re.sub(r"\s*\d+$", "", t.name).lower() == kind:
                x0, y0, x1, y1 = self.frame_box(t, info)
                out.append({"polygons": [[[x0 + 2, y0 + 2], [x1 - 2, y0 + 2], [x1 - 2, y1 - 2], [x0 + 2, y1 - 2]]], "score": 0.9})
        return out

    # --- the slide and what is on it -------------------------------------------------------
    def thing(self, name: str | None) -> Thing | None:
        key = (name or "").lower()
        return next((t for t in self.things if t.name.lower() == key), None) or \
            next((t for t in self.things if key and (key in t.name.lower() or t.name.lower() in key)), None)

    def frame_box(self, thing: Thing, info) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = thing.box
        return x0 + info.dx, y0 + info.dy, x1 + info.dx, y1 + info.dy

    def thing_at(self, x: float, y: float, info) -> Thing | None:
        inside = [t for t in self.things if self.frame_box(t, info)[0] <= x <= self.frame_box(t, info)[2]
                  and self.frame_box(t, info)[1] <= y <= self.frame_box(t, info)[3]]
        return min(inside, key=lambda t: (t.box[2] - t.box[0]) * (t.box[3] - t.box[1]), default=None)

    def check_reply(self, images) -> dict:
        """The outline check's verdict: the case's first `check` rule `on` the thing the magenta outline in the view
        is drawn around (found in the picture, as the model sees it); `box_of` names the thing whose box it gives."""
        import cv2
        import numpy as np

        view = cv2.imdecode(np.frombuffer(images[0], np.uint8), cv2.IMREAD_COLOR).astype(int)
        b, g, r = view[..., 0], view[..., 1], view[..., 2]
        ys, xs = np.nonzero((r - g > 100) & (b - g > 40))
        info = self.hub.latest_info
        on = self.thing_at(xs.mean() / view.shape[1] * WIDTH, ys.mean() / view.shape[0] * HEIGHT, info) if len(xs) else None
        rule = next((c for c in self.case["check"] if on is not None and c["on"].lower() == on.name.lower()), None)
        reply = dict(rule["reply"]) if rule else {"fits": "yes"}
        if reply.get("box_of"):
            reply["box_2d"] = frame_box_to_gemini(self.frame_box(self.thing(reply.pop("box_of")), info), Crop.full(WIDTH, HEIGHT))
        return reply

    def look(self, names: list[str]) -> None:
        """The running look (orchestrator/sight.py) sees these things now: s1, s2..."""
        info = self.hub.latest_info
        seen = [Seen(f"s{i + 1}", t.seen_as or t.name, "", self.frame_box(t, info), 0.8)
                for i, t in enumerate(filter(None, (self.thing(n) for n in names)))]
        said = self.case.get("board", {}).get("sight", {})  # what the running look said of a real picture
        self.sight.view = SightView(info.id, info.epoch, info.dx, info.dy, 300.0, said.get("summary", "A specimen."),
                                    said.get("specimen", "specimen"), seen, "sharp")

    def outline(self, name: str, source: str = "ai:gemini-3.8-flash"):
        from orchestrator.scene import SceneObject

        thing, info = self.thing(name), self.hub.latest_info
        x0, y0, x1, y1 = self.frame_box(thing, info)
        return self.hub.scene.add(SceneObject(
            id=self.hub.scene.new_id(), kind="highlight", polygons=[[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]],
            anchor_frame=info.id, anchor_epoch=info.epoch, anchor_offset=(info.dx, info.dy), source=source,
            label=thing.name, status="likely",
            made_by={"operation": "outline", "implementation": "edgetam", "policy": "route", "origin": "brain-default",
                     "how": "looked"} if source.startswith("ai") else
            # A click is outlined by the outline model too, its origin the click (tools.outline_at), as in the app.
            {"operation": "outline", "implementation": "edgetam", "policy": "route", "origin": "click"}
            if source == "click" else None))

    def find_reference(self, subject: str, n: int = 1, **_search):  # search_terms, request_context... as the real one
        """A stand-in Wikimedia Commons: a photo of anything asked for, found at once (reference.find_blocking)."""
        from orchestrator import reference

        return reference.Found(b"", 640, 480, f"{subject.capitalize()} under a microscope",
                               "https://commons.wikimedia.org/wiki/File:Bench.jpg", "CC BY-SA 4.0", "A. Botanist",
                               "A. Botanist, CC BY-SA 4.0, commons.wikimedia.org", f"{subject} microscope", n, 0.0)

    async def click(self, name: str) -> None:
        """The user clicks the middle of a thing on the live view (commands.click): one request outlining what is
        under it with the outline model the settings hold now. Its name is the thing's (naming a click is a model
        call the bench stands in for)."""
        from orchestrator import tools
        from orchestrator.ticket import issue, using

        thing, info = self.thing(name), self.hub.latest_info
        x0, y0, x1, y1 = self.frame_box(thing, info)
        request = issue(self.hub, "click", channel="studio")
        try:
            with using(request):
                obj = await tools.outline_at(self.hub, info.id, (x0 + x1) / 2, (y0 + y1) / 2)
                obj.label = thing.name
                self.hub.scene.version += 1
        except Exception as e:  # a click that outlines nothing is logged, as the app logs it
            self.agent.log({"kind": "click", "ok": False, "error": str(e)})
        finally:
            request.seal()

    def restart(self) -> None:
        """The app restarts (App.build): its settings start afresh with what today's preferences keep
        (app.restore_prefs); the day, the conversation and what was made stay, as they do on disk."""
        from orchestrator.app import restore_prefs
        from orchestrator.day import SessionStore

        self.hub.settings = Settings()
        if self.real:
            self.hub.settings.update(self.case.get("settings", {}))  # the run's arm, as the app's launch settings
        restore_prefs(self.hub.settings, self.conversation.store)
        # The session App.build would open (ADR 0017: the one the app was in), read from disk as a start reads it.
        self.resumed = SessionStore.resume(root=self.conversation.store.root, clock=self.conversation.store.clock).sid

    def move(self, dx: float = 0.0, dy: float = 0.0, epoch: int | None = None) -> None:
        """The slide moves. A new epoch (continuity lost) re-finds every outline at once, as the
        template match does after the slide settles."""
        before = self.hub.latest_info
        info = self.hub.slide.move(dx, dy, epoch)
        if info.epoch != before.epoch:
            for o in self.hub.scene.objects.values():
                o.anchor_epoch = info.epoch
                o.anchor_offset = (o.anchor_offset[0] + info.dx - before.dx, o.anchor_offset[1] + info.dy - before.dy)

    def _seed_session(self, spec: dict) -> None:
        """An earlier session on disk, as the app journals one (ADR 0017): its header and name, a conversation with
        what was said and its summary, what was seen, `days_ago` calendar days back (1: yesterday), at noon: never
        that many 24 hours back, which from 00:00 to 01:00 (or after a 23-hour day) is a date further."""
        from orchestrator.day import sid as sids

        store = self.conversation.store
        root = store.root
        day = datetime.date.fromtimestamp(store.clock()) - datetime.timedelta(days=int(spec.get("days_ago", 1)))
        at = datetime.datetime.combine(day, datetime.time(12)).timestamp()
        sid = sids.next_sid(root, time.strftime("%Y-%m-%d", time.localtime(at)))
        events = [{"type": "session-started", "session": sid, "name": spec["name"], "created": at, "by": "user", "at": at},
                  {"type": "thread-opened", "thread": "t1", "n": 1, "title": spec.get("title", ""), "at": at}]
        if spec.get("specimen"):
            events.append({"type": "subject", "thread": "t1", "name": spec["specimen"], "named_by": "user", "at": at + 1})
        for n, (role, text) in enumerate(spec.get("said", []), 1):
            events.append({"type": "turn", "thread": "t1", "id": n, "role": role, "channel": "chat", "text": text,
                           "frame_id": None, "refs": {}, "at": at + 1 + n})
        if spec.get("summary"):
            events.append({"type": "summary", "thread": "t1", "text": spec["summary"], "upto": len(spec.get("said", [])),
                           "at": at + 60})
        folder = root / sid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))

    def _setup(self, board: dict) -> None:
        for spec in board.get("sessions", []):  # earlier sessions on disk (sessions.py's switch_session finds them)
            self._seed_session(spec)
        if board.get("specimen"):  # the specimen named, as day/subject.py sets it (runs then carry its subject)
            self.hub.scene.context["specimen"] = board["specimen"]
        for name in board.get("outlines", []):
            self.outline(name, source="click" if name in board.get("clicked", []) else "ai:gemini-3.8-flash")
        if board.get("magnifier"):
            target = next(o for o in self.hub.scene.objects.values() if o.label.lower() == board["magnifier"].lower())
            self.hub.scene.select(target.id)
            self.magnifier.zoom(target.id)
        if board.get("picture"):  # a picture up in the magnifier, of that kind ("reference": a photo from the web)
            self.magnifier.show_picture(b"", f"a {board['picture']} picture", kind_of=board["picture"])
        if board.get("look") is not None:
            self.look(board["look"])
        if board.get("moved"):
            self.move(**board["moved"])

    def fill(self, reply: dict) -> dict:
        """A scripted reply refers to things by name: "@xylem" is its s-id in the running look
        now, "#xylem" the id of its outline on screen now; "$snapshot" and "$tracks" are the ids of the newest
        snapshot and motion tracks in the session's index (as the model reads them from `resources`)."""
        def one(value):
            if isinstance(value, str) and re.fullmatch(r"@\d+", value):
                return value  # a step reference ("@1": what step 1 of this answer made), as the model writes it
            if value == "$snapshot":
                made = self.hub.resources.list("snapshot")
                return made[-1].id if made else "snapshot-000000-f0"
            if value == "$tracks":
                from orchestrator.motiontracks import tracks_of

                newest = tracks_of(self.hub).latest()
                return newest.id if newest is not None else "m99"
            if isinstance(value, str) and value[:1] in "@#" and len(value) > 1:
                key = value[1:].lower()
                if value[0] == "@":
                    view = self.sight.view
                    hit = next((t for t in (view.things if view else []) if key in t.name.lower()), None)
                    return hit.id if hit else "s99"
                hit = next((o for o in self.hub.scene.objects.values() if key in (o.label or "").lower()), None)
                return hit.id if hit else "o99"
            if isinstance(value, list):
                return [one(v) for v in value]
            if isinstance(value, dict):
                return {k: one(v) for k, v in value.items()}
            return value

        return one(reply)

    # --- what the user is told, and what the board shows ------------------------------------
    def snapshot_shown(self) -> dict | None:
        """The named request's retained PNG and source receipt match the displayed capture."""
        wanted = next((p["snapshot_shown"] for p in self.case.get("expect", {}).get("board", [])
                       if "snapshot_shown" in p), None)
        turn = self.request_turns.get(wanted)
        if turn is None:
            return None
        import cv2
        import numpy as np

        shown = self.magnifier.picture_now() if self.magnifier.open else None
        if not shown or shown.get("media") != "capture":
            return None
        url = shown.get("url") or ""
        report = self.hub.screen.item("magnifier") or {}
        if (not report.get("open") or report.get("source") != url
                or not any(p.get("source") == url for p in report.get("pictures", []))):
            return None
        jpeg = self.magnifier.pictures.get(url.rsplit("/", 1)[-1])
        for run, ctx in self.snapshot_runs:
            if ctx.turn_id != turn or not jpeg or url.rsplit("/", 1)[-1] not in run.pictures:
                continue
            path = self.root / (run.values or {}).get("file", "")
            try:
                path.relative_to(self.services.snapshots)
                sidecar = path.with_suffix(".json")
                stamps = tuple((p.stat().st_size, p.stat().st_mtime_ns) for p in (path, sidecar))
                key = (turn, str(path), url, hashlib.sha256(jpeg).hexdigest(), stamps)
                if key in self._snapshot_proofs:
                    return self._snapshot_proofs[key]
                meta = json.loads(sidecar.read_text())
                entry = self.hub.resources.get(path.stem)
                if (entry is None or entry.kind != "snapshot" or meta.get("frameId") != ctx.frame_id
                        or meta.get("session") != ctx.asked.session
                        or entry.source.get("frame") != meta.get("frameId")):
                    continue
                pixels = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
                if pixels is None:
                    continue
                ok, encoded = cv2.imencode(".jpg", pixels, [cv2.IMWRITE_JPEG_QUALITY, 94])
                if not ok or encoded.tobytes() != jpeg:
                    continue
            except (OSError, ValueError, TypeError, AttributeError, cv2.error):
                continue
            proof = {"request": wanted, "turn": turn, "snapshot": path.stem, "frame": meta["frameId"],
                     "session": meta["session"], "crop": meta["crop"], "source": url, "sha256": key[3]}
            self._snapshot_proofs[key] = proof
            return proof
        return None

    def comparison_shown(self) -> dict | None:
        """Read the displayed composite's actual retained originals, never its title as proof."""
        shown = self.magnifier.picture_now() if self.magnifier.open else None
        if shown is None:
            return None
        url = shown.get("url") or ""
        reported = self.hub.screen.item("magnifier") or {}
        if (not reported.get("open") or reported.get("source") != url
                or not any(picture.get("source") == url for picture in reported.get("pictures", []))):
            return None
        jpeg = self.magnifier.pictures.get(url.rsplit("/", 1)[-1])
        if not jpeg:
            return None
        results = self.hub.resources.results
        matches = []
        for entry in results.entries():
            source = entry.get("source") or {}
            components = source.get("components") or []
            if (entry.get("tool") != "compare" or entry.get("state") != "saved"
                    or source.get("kind") != "composite" or len(components) != 2
                    or not source.get("provenance", {}).get("componentsInOrder")
                    or source.get("digest") != hashlib.sha256(jpeg).hexdigest()
                    or results.picture(entry["id"], 0, entry["day"]) != jpeg):
                continue
            originals = [results.picture(entry["id"], n, entry["day"]) for n in (1, 2)]
            if any(not pixels or hashlib.sha256(pixels).hexdigest() != component.get("digest")
                   for pixels, component in zip(originals, components)):
                continue
            ids = [component.get("provenance", {}).get("outline") for component in components]
            if any(oid not in self.hub.scene.objects for oid in ids) or ids[0] == ids[1]:
                continue
            matches.append({"url": url, "resource": entry["id"], "outlineIds": ids,
                            "labels": [self.hub.scene.objects[oid].label for oid in ids]})
        return matches[0] if len(matches) == 1 else None

    def board(self) -> dict:
        info = self.hub.latest_info
        outlines = [{"id": o.id, "label": o.label, "source": o.source, "box": [round(v) for v in _box_in(o, info)],
                     "madeBy": (o.made_by or {}).get("implementation"),
                     # drawn by a model the request chose (`use`): two of one thing so drawn are kept on purpose
                     "chosen": (o.made_by or {}).get("origin") in ("user-chosen", "brain-chosen")}
                    for o in self.hub.scene.objects.values() if o.kind == "highlight" and o.trial is None]
        evidence = getattr(self.hub, "evidence", None)
        newest = evidence.latest() if evidence is not None else None
        settings = self.hub.settings
        return {"outlines": outlines, "selected": self.hub.scene.selected,
                "snapshot": self.snapshot_shown(),
                # the defaults now (what "from now on" changes and a one-request choice must not)
                "settings": {"vision": settings.vision, "outline": settings.outline, "cells": settings.cells,
                             "fast": settings.fast},
                "populations": [{"id": p.id, "implementation": (p.made_by or {}).get("implementation"),
                                 "for": (p.made_by or {}).get("for")} for p in (evidence.items.values() if evidence else [])],
                "viewer": {"open": bool(self.presenter.open), "rid": self.presenter.rid if self.presenter.open else None},
                "magnifier": {"open": self.magnifier.open, "object": self.magnifier.object_id,
                              "picture": (self.magnifier.picture_now() or {}).get("label"),
                              "comparison": self.comparison_shown(),
                              "requiresRetainedComparison": bool(self.case.get("evidence_scope")),
                              # what kind of picture it shows, and beside the close-up (kind (f), live cases 6 and 7)
                              "shows": (self.magnifier.picture_now() or {}).get("media"),
                              "beside": (self.magnifier.shown_beside() or {}).get("media")},
                "population": {"id": newest.id, "count": len(newest.live())} if newest is not None else None,
                "layer": next(({**i.get("style", {}), "opacity": i.get("opacity")} for k, i in self.hub.studio.items.items()
                               if k.startswith("layer:")), None),
                "outlinesVisible": bool(self.hub.studio.items.get("outlines", {}).get("visible", True)),
                # The session open (ADR 0017): which, its name, its conversations, what it last answered, and the one a
                # restart opens.
                "session": self.session_now(),
                "things": {t.name.lower(): [round(v) for v in self.frame_box(t, info)] for t in self.things}}

    def session_now(self) -> dict:
        day = self.conversation.store
        answers = [t.text for t in day.active.turns if t.role == "assistant"]
        return {"id": day.sid, "name": day.name, "first": day.sid == self.first_session,
                "conversations": len(day.threads), "answer": answers[-1] if answers else "", "resumed": self.resumed}

    def claim(self, text: str, kind: str, by: str, about: str | None = None) -> None:
        self.claims.append(Claim(self.clock.now(), text, kind, by, self.board(), about))

    async def watch(self, stop: asyncio.Event) -> None:
        """Every change of the board, with when it happened; and, for a case whose camera is `live` (or has things
        moving), a new frame of the view every CAMERA_TICK_S of real time, as the camera sends them."""
        last, ticked = None, time.monotonic()
        live = self.case.get("camera") == "live" or bool(self.case.get("movers"))
        while not stop.is_set():
            now = self.board()
            if now != last:
                self.boards.append((self.clock.now(), now))
                last = now
            if live and time.monotonic() - ticked >= CAMERA_TICK_S:
                ticked = time.monotonic()
                self.hub.slide.tick()
            await asyncio.sleep(0.002)
