"""The real app for the capability matrix (tests/test_capability_matrix.py): App.build over stand-in GPU workers
(tests/standin_workers.py), scripted models that answer at once and say which model was asked from which tool, a
stand-in research process that records what it is asked to start, and a generated frame published as the camera's.

Chat asks through the registry (Brain.run_actions: the steps of a request, in order, as typed chat runs them); the voice
hands a request over through the spoken-request handling (brain/speech.py: what GPT-Live's ask_app reaches) with a
scripted voice stand-in; a researcher asks through its hands (research/hands.py: `run`).
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

from tests.standin_workers import build_app
from tests.waiting import HANG_S

LEAF = None  # the generated frame's bytes, made once
FAST_QUESTION = re.compile(r'User: "(.*?)"\n', re.S)
STOMA_GEMINI = [486, 645, 597, 691]  # the leaf frame's stoma (model_checks.STOMA_BOX) as a 0-1000 box_2d
PLAN: ContextVar = ContextVar("matrix_plan", default=None)


def leaf() -> bytes:
    """The rows' generated texture; never depends on the owner's benchmark frames."""
    global LEAF
    if LEAF is None:
        import cv2
        import numpy as np

        noise = np.random.default_rng(3).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
        LEAF = cv2.imencode(".jpg", cv2.GaussianBlur(noise, (0, 0), 2.0))[1].tobytes()
    return LEAF


class Model:
    """A scripted provider: one per option asked for (Agent.vision), keeping every prompt with the tool whose run
    asked it (registry.CURRENT_RUN), so a row can say which model a tool reached."""

    def __init__(self, rig: "Rig", option: str) -> None:
        self.rig, self.option = rig, option

    async def ask(self, images, prompt, max_output_tokens=0):
        from orchestrator.brain.registry import CURRENT_RUN

        run = CURRENT_RUN.get()
        self.rig.asked.append((self.option, run.tool if run is not None else None, prompt[:120]))
        return SimpleNamespace(data=self.rig.reply(prompt), latency_ms=1.0, error=None, model=self.option,
                               usage={"input": 10, "output": 5}, raw_text="")


class ResearchProcess:
    """The research process as the manager talks to it (research/manager.py `_launch`): what it is asked to start is
    kept; it starts nothing."""

    alive = True

    def __init__(self) -> None:
        self.spawned: list[dict] = []
        self.calls: list[tuple[str, dict]] = []

    async def call(self, op, **payload):
        self.calls.append((op, payload))
        if op == "spawn":
            self.spawned.append(payload)
        return {"ok": True}

    def send(self, *a, **k):
        pass

    def stop(self):
        pass


class Rig:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.app = build_app(self.root)
        self.hub, self.brain = self.app.hub, self.app.brain
        self.asked: list[tuple[str, str | None, str]] = []
        self.models: dict[str, Model] = {}
        self.app.agent.vision = self.model
        self.runs: list = []  # (ToolRun, channel) of every tool started through the registry, in order
        start = self.brain.registry.start

        def started(name, args, ctx, run=None):
            made = start(name, args, ctx, run)
            self.runs.append((made, getattr(ctx, "channel", None)))
            return made

        self.brain.registry.start = started
        self.research = ResearchProcess()

        async def worker():
            return self.research

        self.app.research._ensure_worker = worker
        self.hub.source.stale_seconds = 3600.0  # the camera stays live through a row, however long its tools wait
        self.publish(1, ago=6.0)  # older than any still clip a row adds (the clip buffer keeps frames in time order)

    # --- the scripted world -----------------------------------------------------------------------------------------
    def model(self, option: str) -> Model:
        if option not in self.models:
            self.models[option] = Model(self, option)
        return self.models[option]

    def reply(self, prompt: str) -> dict:
        """What a model says: the row's plan to the fast pass; plain facts to an answer round; to a tool's own question,
        one stoma where it is, an answer, a fit."""
        if "<answer-facts" in prompt or "Original request and completed receipts" in prompt:
            return {"answer": "Done what the receipts say.", "actions": [], "observation_scope": "none", "input_refs": []}
        if prompt.startswith("You write a short document for a scientist"):  # write_document: one record cited
            cited = re.findall(r"^\[([^\]]+)\]", prompt, re.M)[:1]
            return {"title": "Findings", "sections": [{"heading": "What was found", "paragraphs": [
                {"text": "What the newest record says.", "cites": cited}]}]}
        tiles = re.search(r"contact sheet of \d+ numbered tiles.*?\(tiles (\d+) to (\d+)\)", prompt, re.S)
        if tiles:  # classify (classify.py): each numbered tile named, the first as one class, the rest as the other
            first, last = int(tiles.group(1)), int(tiles.group(2))
            return {"tiles": [{"n": n, "class": "guard cell" if n == first else "pavement cell", "sure": True}
                              for n in range(first, last + 1)]}
        plan = PLAN.get()
        if plan is not None and FAST_QUESTION.search(prompt) and "First, what are" in prompt:
            return {"intent": "request", "needs_look": False, "answer": "On it.", "actions": plan,
                    "observation_scope": "none", "input_refs": []}
        return {"answer": "A stoma with two guard cells.", "label": "stoma", "confidence": 0.8, "group": "stoma",
                "targets": [{"label": "stoma", "box_2d": STOMA_GEMINI, "confidence": 0.8}],
                "things": [{"label": "stoma", "box_2d": STOMA_GEMINI}], "fits": "yes", "yes": True, "seen": True,
                "summary": "two parts", "parts": [{"n": 1, "name": "guard cell", "confidence": 0.8, "box_2d": STOMA_GEMINI}],
                "numbers": [1, 2], "note": "",
                "same": [], "differences": ["one is larger"], "observation_scope": "none", "input_refs": []}

    def find_reference(self, subject: str, n: int = 1, **_search):
        from orchestrator import reference

        return reference.Found(b"", 640, 480, f"{subject.capitalize()} under a microscope",
                               "https://commons.wikimedia.org/wiki/File:Bench.jpg", "CC BY-SA 4.0", "A. Botanist",
                               "A. Botanist, CC BY-SA 4.0, commons.wikimedia.org", f"{subject} microscope", n, 0.0)

    def publish(self, fid: int, dx: float = 0.0, dy: float = 0.0, epoch: int = 0, ago: float = 0.0) -> None:
        """The generated frame as the camera's frame `fid`, `ago` seconds ago, through the hub's own publishing
        (Hub._publish)."""
        from orchestrator.fov import FieldOfView
        from orchestrator.frames import Frame
        from orchestrator.hub import FrameInfo

        now = time.time() - ago
        frame = Frame(fid, leaf(), time.monotonic() - ago, now, 9000 + fid, "matrix")
        info = FrameInfo(fid, 1280, 720, dx, dy, epoch=epoch, tracked=True, steady=True,
                         fov=FieldOfView(695.0, 360.0, 395.0, 0.99), wall_at=now, seq=frame.seq, boot=frame.boot)
        self.hub.source.ring[fid] = frame
        self.hub.source.last_frame_at = time.monotonic()  # the camera is live now, whatever the frame's own time
        self.hub._publish(frame, info)

    # --- the three actors ---------------------------------------------------------------------------------------------
    async def turn(self, plan: list[tuple[str, dict]], words: str = "") -> list:
        """A typed request (Brain.turn, channel chat): its fast pass plans these steps (the scripted model's answer),
        and they run through the registry in order, as typed chat runs them. The ToolRuns of its steps."""
        before = len(self.runs)
        token = PLAN.set([{"tool": tool, "args": args} for tool, args in plan])
        try:
            await asyncio.wait_for(self.brain.turn(words or "do it", "chat"), 20.0)
        finally:
            PLAN.reset(token)
        await self.settle([r for r, _ in self.runs[before:]])
        return [r for r, channel in self.runs[before:] if channel == "chat"]

    chat = turn

    async def voice(self, plan: list[tuple[str, dict]], words: str) -> list:
        """A spoken request handed over: heard, then GPT-Live's ask_app (SpokenRequests.handoff), its plan the fast
        pass's (the scripted model's), carried out by hands on the voice's own context."""
        from orchestrator.brain import speech
        from orchestrator.brain.speech import SpokenRequests

        before = len(self.runs)
        token = PLAN.set([{"tool": tool, "args": args} for tool, args in plan])
        saved = {k: getattr(speech, k) for k in TIMINGS}
        for k, v in TIMINGS.items():
            setattr(speech, k, v)
        try:
            spoken = SpokenRequests(self.brain, None, Voice(), clock=time.monotonic)
            spoken.heard(words, None)
            spoken.handed_over()
            # This matrix checks capability wiring with a settled transcript. Await its real
            # join/Hands task (still inside the row's measured time), instead of making every
            # row pay _bind's 50 ms polling tick. Transcript/handoff races have their own tests.
            if spoken.pending is not None:
                await asyncio.wait_for(asyncio.shield(spoken.pending), 20.0)
            found = await asyncio.wait_for(spoken.handoff(words, "d1"), 20.0)
        finally:
            for k, v in saved.items():
                setattr(speech, k, v)
            PLAN.reset(token)
        await self.settle([r for r, _ in self.runs[before:]])
        return [r for r, channel in self.runs[before:] if channel == "voice"], found

    # --- what was asked of whom, since a mark -------------------------------------------------------------------------
    def workers(self) -> list:
        # A big model's own worker too (hub.host_for: SAM 3.1, micro-sam), made when first asked for.
        return [w for w in (self.hub.gpu, self.hub.gpu_heavy, self.hub.gpu_follow, self.hub.gpu_molmo,
                            *getattr(self.hub, "hosts", {}).values()) if w is not None]

    def mark(self) -> dict:
        return {"workers": {w.name: len(w.calls) for w in self.workers()}, "asked": len(self.asked),
                "spawned": len(self.research.spawned), "runs": len(self.runs), "executions": self.executions()}

    def executions(self) -> set[str]:
        """The operation runner's own records so far (operations.Execution ids): what was really dispatched."""
        from orchestrator.operations import runner

        return set(runner(self.hub).executions)

    def executed_since(self, since: dict) -> list[str]:
        """The implementations the runner really used since the mark (its Execution.executed), whoever ran them."""
        from orchestrator.operations import runner

        records = runner(self.hub).executions
        return [r.executed for key, r in records.items() if key not in since.get("executions", set()) and r.executed]

    def calls_since(self, since: dict) -> list[tuple[str, str, dict]]:
        """(worker, op, payload) asked since the mark."""
        return [(w.name, op, p) for w in self.workers() for op, p in w.calls[since["workers"].get(w.name, 0):]]

    def fill(self, args: dict, places: dict | None = None) -> dict:
        """A plan's names for what the setup made ("r1": the finished researcher, "r2": the working one, "@picture":
        the picture the magnifier shows) as their ids, and the row's model where a step names it ("{name}")."""
        ids = {**getattr(self, "ids", {}), **(places or {})}
        pictures = [s.get("url") for s in self.app.magnifier.sources if s.get("kind") == "image"]
        if pictures:
            ids["@picture"] = pictures[0]

        def one(value):
            if isinstance(value, str) and value in ids:
                return ids[value]
            if isinstance(value, list):
                return [one(v) for v in value]
            if isinstance(value, dict):
                return {k: one(v) for k, v in value.items()}
            return value

        return one(args)

    # --- the board before a row ----------------------------------------------------------------------------------------
    async def outline(self, box, label: str):
        from orchestrator import tools

        info = self.hub.latest_info
        return await tools.outline_at(self.hub, info.id, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, box=box,
                                      source="ai:matrix", label=label, status="likely")

    def look(self) -> None:
        """The running look (sight.py) sees the stoma now: s1."""
        from orchestrator.model_checks import STOMA_BOX
        from orchestrator.sight import Seen, SightView

        info = self.hub.latest_info
        self.app.sight.view = SightView(info.id, info.epoch, info.dx, info.dy, 300.0, "A leaf epidermis with stomata.",
                                        "leaf epidermis", [Seen("s1", "stoma", "", STOMA_BOX, 0.8)], "sharp")

    def still(self, n: int = 40, seconds: float = 4.5) -> None:
        """The slide at rest for the last few seconds: frames of the same view, the newest now."""
        start = (self.hub.latest_info.id if self.hub.latest_info is not None else 0) + 1
        for k in range(n):
            self.publish(start + k, ago=seconds - seconds * k / max(1, n - 1))
        self.hub.source.last_frame_at = time.monotonic()

    def make_researcher(self, n: int, status: str, *, lesson: bool = True, asked: bool = False):
        """A researcher of today in the app's own manager: finished with a lesson (n=1), or still working (n=2)."""
        from orchestrator.research import Session
        from research_worker.results import lesson_content

        rid = f"r9{n:03d}"
        root = self.app.research.root
        folder = root / self.app.day.day / rid / "outputs"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "lesson.html").write_text("<html><body><h1>Guard cells</h1><p id='q1'>What do guard cells control?</p></body></html>")
        content = lesson_content("Guard cells", ["two guard cells surround a pore"], [
            {"question": "What do guard cells control?", "answer": "the pore's opening", "hint": "gas exchange"},
            {"question": "Find a guard cell on your slide and click it.", "answer": "a bean-shaped cell", "kind": "find",
             "target": "guard cell"}])
        outputs = [{"id": "o1", "kind": "html", "path": "outputs/lesson.html", "caption": "Interactive lesson",
                    "content": content, "version": 1},
                   {"id": "o2", "kind": "image", "path": "outputs/labelled.jpg", "caption": "Labelled guard cells",
                    "content": {"kind": "annotated", "labels": [{"name": "guard cell", "where": "left", "box": [0.2, 0.2, 0.5, 0.6]}]},
                    "version": 1}] if lesson else []
        if lesson:
            (folder / "labelled.jpg").write_bytes(leaf())
        session = Session(rid=rid, n=n, brief="a lesson on guard cells", deliverable="lesson" if lesson else None,
                          effort="standard", engine="gpt-6.1-sol", purpose="research", label="guard cells", object_id=None,
                          origin={"channel": "chat", "thread": self.app.day.active_id}, day=self.app.day.day, status=status,
                          created=time.time() - 60, finished=time.time() - 5 if status == "done" else None,
                          outputs=outputs, result={"title": "Guard cells", "summary": "Guard cells open and close the pore."}
                          if status == "done" else None, sitting=self.app.day.day, root=root)
        if asked:
            session.requests.append({"id": "q1", "what": "15 seconds of this field", "why": "to see them move", "seconds": 15,
                                     "at": time.time(), "status": "asked"})
        self.app.research.sessions[rid] = session
        self.app.research._save(session)
        self.ids = {**getattr(self, "ids", {}), f"r{n}": rid}
        return session

    async def researcher(self, operation: str, implementation: str | None = None, **more) -> dict:
        """A researcher's `run` (research/hands.py), as the research process asks for it."""
        from orchestrator.research import Session

        session = Session(rid="r9", day=self.app.day.day, n=9, brief="how much do they vary", deliverable="",
                          effort="standard", engine="fake", purpose="research", label="", object_id=None,
                          origin={"channel": "chat", "thread": self.app.day.active_id}, status="running")
        session.root = self.root / "research"
        return await self.app.research.hands.run(session, operation, "a capability check", implementation=implementation,
                                                 **more)

    async def settle(self, runs) -> None:
        """Steps still running after their answer (effect tools past inline_ms) are awaited, as their late reports are."""
        for run in runs:
            task = getattr(run, "task", None)
            if task is not None and not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), HANG_S)
                except Exception:
                    pass


    # --- what a row found ---------------------------------------------------------------------------------------------
    def judge(self, runs, row, plan, since: dict, world: dict, why: str = "", declared: str = "") -> str:
        """"" when the row's request did what it named and reached the model it named; else what went wrong. `why`: the
        row's model can't run here, so the request must refuse it saying exactly that; `declared`: this path does not
        reach the row's role, and why (the plan says)."""
        planned = [tool for tool, _ in plan.steps]
        mine = [r for r in runs if r.tool in planned]
        chosen = [r for r in runs if r.tool == "use" and "use" not in planned]
        if why and not declared:
            said = [str(r.summary) for r in runs if r.ok is False] + [str(r.summary) for r in mine if plan.reach == "listed"]
            if any(why in s for s in said):
                return ""
            return f"its model can't run here ({why}), and no step said so: {[(r.tool, r.ok, str(r.summary)[:120]) for r in runs]}"
        if chosen and chosen[0].ok is not True:
            return f"the choice was refused: {chosen[0].summary}"
        if not mine:
            return f"its tool never ran on this path (ran: {[r.tool for r in runs]})"
        last = mine[-1]
        if plan.expect == "ok" and last.ok is not True:
            return f"{last.tool} did not do it: {last.summary!r}"
        if plan.expect.startswith("refused: "):
            words = plan.expect.partition(": ")[2]
            if last.ok is not False or words not in str(last.summary):
                return f"{last.tool} was to refuse saying {words!r}; it said {last.summary!r} (ok={last.ok})"
        if row.role is None or declared:
            return ""
        if plan.reach == "listed":
            from orchestrator import names

            entry = __import__("orchestrator.model_checks", fromlist=["find"]).find(row.role, row.option)
            listed = str(last.summary)
            label = names.said_impl(entry.probe[1]).split(" (")[0] if entry.probe[0] != "vision" else entry.name
            return "" if label.split(" on this Mac")[0] in listed else f"the listing never names {label}: {listed[:200]!r}"
        if plan.reach == "set":
            group = {"embed": "embed"}.get(row.role, row.role)
            now = getattr(self.hub.settings, group, None)
            return "" if now == row.option else f"the setting is {now!r} after it, not {row.option!r}"
        return self.reached(row, since, world, last.tool)

    def judge_run(self, reply: dict, row, since: dict, world: dict, why: str = "") -> str:
        if why:
            return "" if not reply.get("ok") and why in str(reply.get("error")) else \
                f"its model can't run here ({why}), and the run did not say so: {reply}"
        if not reply.get("ok"):
            return f"the researcher's run was refused: {reply.get('error')} (next: {reply.get('next')})"
        return self.reached(row, since, world, None) if row.role == "cells" else ""

    def reached(self, row, since: dict, world: dict, tool: str | None) -> str:
        """"" when the model the row named was asked (what the stand-ins kept since the mark), else what was asked."""
        from gpu_worker.loading import model_of
        from orchestrator import model_checks
        from orchestrator.operations import OPERATIONS

        entry = model_checks.find(row.role, row.option)
        kind, what = entry.probe
        if row.role == "reference_search":  # where the photo is searched for: the web always, Commons only when chosen
            web = world["looked"][since["looked"]:]
            catalogue = world["catalogued"][since.get("catalogued", 0):]
            if not web:
                return "the web search was never asked"
            if (row.option == "web+commons") != bool(catalogue):
                return f"Commons' catalogue was {'asked' if catalogue else 'not asked'} with {row.option}"
            return ""
        if row.role == "fixed:retrieval":  # Gemini with Google Search, asked through research_worker.retrieval.lookup
            return "" if world["looked"][since["looked"]:] else "the lookup model was never asked"
        calls = self.calls_since(since)
        asked = [(o, t) for o, t, _ in self.asked[since["asked"]:]]
        spawned = self.research.spawned[since["spawned"]:]
        if kind in ("outline", "cells", "embed"):
            op = {"outline": OPERATIONS["outline"], "cells": OPERATIONS["cells"], "embed": OPERATIONS["looks"]}[kind]
            impl = op.impl(what)
            if impl.worker == "cpu":  # it runs in the app's own process (no GPU worker): the runner's record of what it ran
                done = self.executed_since(since)
                return "" if what in done else f"the runner executed {done or 'nothing'}, not {what}"
            wanted = impl.steps[-1] if impl.steps else what
            ops = {"outline": ("segment",), "cells": ("cells", "automask"), "embed": ("embed",)}[kind]
            got = [model_of(o, p) if o != "automask" else "edgetam-automask" for _w, o, p in calls if o in ops]
            return "" if wanted in got else f"asked {got or 'nothing'} of the workers, not {wanted}"
        if kind == "fal":
            want = "segment_all" if what == "sam3.1-all" else "segment_box"
            return "" if want in world["fal"][since.get("fal", 0):] else f"SAM 3.1 on fal was never asked ({want})"
        if kind == "zeroshot":
            return "" if any(o == "zeroshot" for _w, o, _p in calls) else "MedSigLIP was never asked"
        if kind == "regions":
            return "" if any(o == "regions" for _w, o, _p in calls) else "EdgeTAM's regions were never asked"
        if kind == "vision":
            if row.role == "fast":
                return "" if (what, None) in asked else f"the fast pass asked {sorted({o for o, t in asked if t is None})}"
            return "" if (what, tool) in asked else f"{tool} asked {sorted({o for o, t in asked if t == tool}) or 'no model'}"
        if kind in ("text", "image", "video"):
            if not spawned:
                return "the research process was asked to start nothing"
            job = spawned[-1]
            field = {"research": "engine", "image": "image_model", "claim_check": "claim_check",
                     "lesson_check": "lesson_check"}.get(row.role)
            if field is not None:
                return "" if job.get(field) == row.option else f"the research process was asked for {field}={job.get(field)!r}"
            return ""  # a model fixed in the research process's own code: reached by starting it (its check is the bench's)
        if kind == "link":
            return "" if world["linked"][since.get("linked", 0):] else "Trackastra was never asked"
        return f"no way to see whether {kind} was reached"

    def mark_world(self, since: dict, world: dict) -> dict:
        return {**since, "fal": len(world["fal"]), "looked": len(world["looked"]), "linked": len(world["linked"]),
                "catalogued": len(world["catalogued"])}


STAND_IN_KIND = {"key": "stand-in-describer", "id": "stand-in-describer", "name": "Stand-in describer",
                 "what": "Describes the view in words", "operation": "describe-view", "output": "text",
                 "says": "Stand-in describer: Describes the view in words (gives words)"}


def turn_on_kind(rig: Rig) -> None:
    """A new kind of job on, as env_runner.register makes one (kinds.py): its operation with a stand-in adapter, and the
    agent's run_capability refreshed (undone by turn_off_kind after the row)."""
    from orchestrator import kinds, names, operations
    from orchestrator.brain import toolbox

    k = STAND_IN_KIND
    impl = operations.Impl(k["id"], k["name"], "model", ("field",), "analysis", None, (), version="stand-in 1",
                           note="a new kind of job, in its own environment")
    operations.OPERATIONS[k["operation"]] = operations.Operation(
        k["operation"], k["what"], "text/1", None, (impl,), empty_is_result=True, output=("picture", "rows", "text", "points"))

    async def adapter(run, capture, prompt, impl, params) -> dict:
        return {"text": f"a {capture.width}x{capture.height} view", "output": "text"}

    operations.ADAPTERS[(k["operation"], k["id"])] = adapter
    kinds.registered().add(k["operation"])
    names.index.cache_clear()
    rig.app.services.kinds = lambda: [dict(k)]
    toolbox.refresh(rig.app.brain.registry, rig.app.services)


def turn_off_kind(rig: Rig) -> None:
    from orchestrator import kinds, names, operations

    k = STAND_IN_KIND
    operations.OPERATIONS.pop(k["operation"], None)
    operations.ADAPTERS.pop((k["operation"], k["id"]), None)
    kinds.registered().discard(k["operation"])
    names.index.cache_clear()
    rig.app.services.kinds = kinds.available
    rig.app.brain.registry.tools.pop("run_capability", None)


async def prepare(rig: Rig, setup) -> None:
    """What must be on the board before a row: outlines, the running look, cells, a still slide, researchers..."""
    from orchestrator.model_checks import STOMA_BOX

    cells = 0
    for what in setup:
        if what == "outline":
            await rig.outline(STOMA_BOX, "stoma")
        elif what == "outline2":
            await rig.outline((600.0, 300.0, 660.0, 350.0), "guard cell")
        elif what == "look":
            rig.look()
        elif what == "still":
            rig.still()
        elif what == "cells":
            if cells:
                rig.publish(rig.hub.latest_info.id + 1)  # a second view: its own population
            cells += 1
            await rig.turn([("outline_cells", {})], "outline all the cells")
        elif what == "magnifier":
            await rig.turn([("zoom", {"object": "o1"})], "magnify that")
        elif what == "reference":
            await rig.turn([("reference_photo", {"subject": "stoma", "beside": True})], "show me a real stoma beside it")
        elif what == "objective":
            await rig.turn([("set_objective", {"objective": 40})], "I'm on 40x")
        elif what == "kind":
            turn_on_kind(rig)
        elif what == "lesson":
            rig.make_researcher(1, "done")
        elif what == "working":
            rig.make_researcher(2, "running", lesson=False)
        elif what == "asked":
            rig.make_researcher(2, "running", lesson=False, asked=True)
        elif what == "seen":
            # An earlier view of today, as the running look saves it (a "sight" record and its picture, memory.py).
            from datetime import datetime, timedelta

            day = rig.app.day.day
            crop = rig.app.paths.interaction_log.parent / "crops" / day / "earlier.jpg"
            crop.parent.mkdir(parents=True, exist_ok=True)
            crop.write_bytes(leaf())
            at = (datetime.now() - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S")
            with rig.app.paths.interaction_log.open("a") as f:
                f.write(json.dumps({"at": at, "kind": "sight", "image": f"crops/{day}/earlier.jpg",
                                    "specimen": "leaf epidermis", "things": ["stoma"]}) + "\n")
        elif what == "blood":
            await rig.turn([("name_specimen", {"name": "blood smear"})], "this is a blood smear")
        else:
            raise ValueError(f"unknown setup {what!r}")


# The spoken path's delays, a millisecond here so a row doesn't sit through them. HANDS_RESULT_S only bounds a hand-off's
# wait for hands to decide, which ends when hands does: at a millisecond, a loaded Mac's hands missed it and the look
# answered instead (the gate of 2026-10-16), so it is a hang's limit.
TIMINGS = {"JOIN_PIECES_S": 0.001, "JOIN_UNFINISHED_S": 0.001, "WAIT_FOR_WORDS_S": 0.001, "WORDS_GRACE_S": 0.001,
           "POINT_AFTER_S": 0.001, "HANDS_KEPT_S": 0.001, "HANDS_RESULT_S": HANG_S}


class Voice:
    """GPT-Live as the acting bench's stand-in (bench/acting/world.py Voice): told things silently, says nothing."""

    closed = False

    def __init__(self) -> None:
        self.told: list[str] = []

    async def inject(self, text: str, respond: bool = False, **_kw) -> None:
        self.told.append(text)


def dumps(value) -> str:
    return json.dumps(value, default=str)[:300]
