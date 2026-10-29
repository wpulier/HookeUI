"""The one-brain voice's tools: a curated part of the brain's registry, called by the model with its own arguments, plus
three of its own (`mark`, `look_hard`, `do_in_hooke`). PARTNER-CONTRACT "Tools".

Each registry tool is offered in the form the realtime API takes (`Registry.responses_tools`, reshaped) and run the way
the brain runs it: one request of its own (a voice Ticket with its admission and request id), a voice `ToolContext`
whose actor is the Assistant and whose voice owner is this session (researchers it starts report back to it), the
accepted studio's pin when there is one, and the brain's `ActionRunner` (outline references, the board, late steps).
What it did goes into the chat as the brain's steps do (`chat-action` rows) and its receipt back to the model.

Act, then look: after a tool that changes the screen, the studio is asked for a fresh report and a fresh window picture,
which go back with the result (`_look`), so the model sees whether it worked before it says so. A tool still running
after TOOL_WAIT_S is answered "still working"; its outcome is said later as one line (a long tool done).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from .base import ToolSpec

# The registry's tools the voice gets, by name (tests/golden/tool_schemas.json): those this registry has are offered.
# Web search is retrieve_page; a capability by name is run_capability (there once one is turned on); pictures are
# reference_photo; documents and windows are present.
PARTNER_TOOLS = ("present", "tabs", "show_measurements", "highlight", "find", "outline_cells", "select", "zoom", "clear",
                 "start_researcher", "research_status", "researcher_note", "cancel_researcher", "show_me", "resources",
                 "models_in_use", "capabilities", "workflow", "new_project", "retrieve_page", "run_capability",
                 "reference_photo")
# Tools that change what is on screen: the model gets a fresh look after them (act, then look).
SCREEN_CHANGING = frozenset({"present", "tabs", "show_measurements", "highlight", "find", "outline_cells", "select",
                             "zoom", "clear", "new_project", "reference_photo", "show_me", "run_capability"})
# One call per user turn: the same call again in the same turn returns the first one's result (one request, one
# researcher; live 2026-10-28: two researchers from one request).
ONCE_PER_TURN = frozenset({"start_researcher", "new_project", "cancel_researcher"})
TOOL_WAIT_S = 12.0  # a tool still running after this is answered "still working"; its outcome follows as one line
WORDS_WAIT_S = 1.5  # a tool waits at most this long for the user's words of its turn (their transcription)
REPORT_WAIT_S = 2.0  # act, then look: the studio's fresh report, at most
MARKS_WAIT_S = 1.0  # the studio's marks-shown, at most
STYLES = ("box", "point", "underline")

MARK = {
    "type": "object",
    "properties": {
        "targets": {"type": "array", "minItems": 1, "maxItems": 6, "items": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": (
                    "What to point at, by Hooke's own id: outline:<id> (one outline), population:<id> (a whole layer of "
                    "cell outlines, e.g. population:e1), member:<population>:<member>, window:<id>, "
                    "document:<placement>, tab:<id or exact title>, researcher:<number or id>, panel:<name> "
                    "(magnifier, viewer, slidemap, card, made-today, sidebar, live, workspace-tabs), or \"frame\" with "
                    "a region or polygon in the camera picture you saw.")},
                "region": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                           "description": "frame only: [x, y, w, h], 0-1 of the camera picture"},
                "polygon": {"type": "array", "items": {"type": "array", "items": {"type": "number"},
                                                       "minItems": 2, "maxItems": 2},
                            "description": "frame only: [[x, y], ...], 0-1 of the camera picture"},
            },
            "required": ["target"]}},
        "style": {"type": "string", "enum": list(STYLES)},
        "label": {"type": "string", "maxLength": 60},
    },
    "required": ["targets"],
}


def realtime_tool(declaration: dict) -> dict:
    """A Responses function declaration as the realtime API takes it: name, description and parameters, without the
    brain's plan metadata (after_steps)."""
    params = json.loads(json.dumps(declaration.get("parameters") or {"type": "object"}))
    params.get("properties", {}).pop("after_steps", None)
    return {"name": declaration["name"], "description": declaration.get("description") or "", "parameters": params}


class PartnerTools:
    """The tools of one partner session. `partner` (voice/partner.py) gives the turn's words, the studio link, the
    camera and the screen as text."""

    def __init__(self, brain, hub, partner, *, client=None, dialogue=None) -> None:
        self.brain, self.hub, self.partner, self.client, self.dialogue = brain, hub, partner, client, dialogue
        self._once: dict[tuple, asyncio.Future] = {}  # (turn, tool) -> its result, for ONCE_PER_TURN
        self._screen = asyncio.Lock()  # tools that change the screen run one after another (parallel tool calls)
        self.reported: set[str] = set()  # action ids whose outcome went back to the model
        self.fail_next: str | None = None  # the bench's fault hook (MICROMIND_BENCH_FAULTS): the next call fails

    # --- what the model is offered ------------------------------------------------------------------
    def names(self) -> list[str]:
        tools = getattr(self.brain.registry, "tools", {})
        return [name for name in PARTNER_TOOLS if name in tools]

    def specs(self) -> list[ToolSpec]:
        declared = {d["name"]: d for d in self.brain.registry.responses_tools()}
        specs = []
        for name in self.names():
            shaped = realtime_tool(declared[name])
            specs.append(ToolSpec(name, shaped["description"], shaped["parameters"], self._runner(name)))
        specs.append(ToolSpec("mark", "Point at things on Hooke's screen while you talk about them: an outline, window, "
                              "Tab, researcher card or panel by its id, or a region of the camera picture. Returns which "
                              "marks the studio drew.", MARK, self.mark))
        specs.append(ToolSpec("look_hard", "Look closely at the camera picture (and Hooke's window if needed) with the "
                              "app's strongest vision model when a detail matters and you are unsure. Returns its answer.",
                              {"type": "object", "properties": {
                                  "question": {"type": "string"},
                                  "window": {"type": "boolean", "description": "also send Hooke's window picture"}},
                               "required": ["question"]}, self.look_hard))
        specs.append(ToolSpec("do_in_hooke", "Rarely: hand a request with several steps that no other tool covers to "
                              "Hooke's own assistant, in the user's full words. Returns what it found and did.",
                              {"type": "object", "properties": {"request": {"type": "string"}},
                               "required": ["request"]}, self.do_in_hooke))
        return specs

    def _runner(self, name: str):
        async def run(args: dict) -> dict:
            return await self.run(name, args)
        return run

    # --- a registry tool ---------------------------------------------------------------------------
    async def run(self, name: str, args: dict) -> dict:
        args = {k: v for k, v in (args or {}).items() if not k.startswith("_") and k != "after_steps"}
        n = self.partner.turn()
        if self.fail_next is not None:
            why, self.fail_next = self.fail_next, None
            return {"ok": False, "error": why, "_log": {"fault": "bench"}}
        if name not in ONCE_PER_TURN:
            if name in SCREEN_CHANGING:
                async with self._screen:  # changes on screen one after another, each with its own look after it
                    return await self._run(name, args, n)
            return await self._run(name, args, n)
        # One per request (one researcher, one Project): the turn's slot is taken before anything awaits, so a second
        # call of it in the same turn, in parallel or later and whatever its words, waits for the first's result.
        key = (n, name)
        first = self._once.get(key)
        if first is not None:
            result = await asyncio.shield(first)
            return {**{k: v for k, v in result.items() if not k.startswith("_")},
                    "note": "already done for this request; not done again"}
        self._once = {k: v for k, v in self._once.items() if k[0] == n}  # only this turn's are kept
        slot = self._once[key] = asyncio.get_running_loop().create_future()
        try:
            result = await self._run(name, args, n)
        except BaseException as exc:
            slot.set_result({"ok": False, "error": f"it did not finish ({type(exc).__name__})"})
            raise
        slot.set_result(result)
        return result

    async def _run(self, name: str, args: dict, n: int) -> dict:
        words = await self.partner.words(n, WORDS_WAIT_S)
        before = self._reported_at()
        run, ctx = await self._start(name, args, words)
        log: dict[str, Any] = {"requestId": getattr(getattr(run, "occurrence", None), "request_id", None)}
        if run.ok is None and run.task is not None:
            await asyncio.wait([run.task], timeout=TOOL_WAIT_S)
        result = self._result(run)
        self._file(run, ctx)
        if run.ok is not None and getattr(run.occurrence, "action_id", None):
            self.reported.add(run.occurrence.action_id)
        if name == "start_researcher" and run.ok:
            rid = self._researcher_of(run)
            if rid:
                log.update(rid=rid, ridT=time.time())
                result["researcher"] = rid
        if name in SCREEN_CHANGING and run.ok is not False:
            look, facts = await self.look_after(name, before)
            result["_look"] = look
            log.update(facts)
            points = self.points_of(run)
            if points and run.ok:
                # Pointed at by the app as the answer about it is heard: the model need not call mark for it.
                result["_point"], result["pointed_at"] = points, points
        result["_log"] = log
        return result

    async def _start(self, name: str, args: dict, words: str):
        """The tool as the brain runs it, in a voice request of its own, attributed to the Assistant by voice."""
        from ..brain.actions import ActionRunner
        from ..brain.registry import ToolContext
        from ..ticket import issue, using

        brain, hub = self.brain, self.hub
        conversation = brain.conversation
        request = issue(hub, "turn", asked=conversation.here(), channel="voice")
        with using(request):
            try:
                info = hub.latest_info
                board = getattr(brain, "board", None)
                # "That" is what was pointed at when the user pressed talk (board.WhenAsked), not after.
                asked_at = self.partner.when_asked(self.partner.turn())
                if asked_at is None and board is not None:
                    asked_at = board.when_asked()
                ctx = ToolContext("voice", 0, info.id if info else None, question=words,
                                  asked=request.asked, admission=request.admission,
                                  request_id=request.admission.request_id, voice_owner=self.partner.session,
                                  when_asked=asked_at)
                if self.client is not None:
                    with contextlib.suppress(Exception):  # no current report: the tool runs on the app's own view
                        pin = hub.screen.pin(self.client, request_id=request.admission.request_id,
                                             timing="server-handoff-arrival")
                        ctx.client, ctx.request_snapshot = self.client, pin
                        ctx.workspace_id = pin.observation.client.workspace_id
                selector = getattr(getattr(brain, "sight", None), "select", None)
                if callable(selector):
                    with contextlib.suppress(Exception):
                        ctx.sight_selection = selector(request)
                runner = ActionRunner(brain.registry, hub, lambda: brain.sight, self._late, board,
                                      log=getattr(brain.agent, "log", None))
                runs = await runner.run([(name, args)], ctx)
            finally:
                request.seal()
        return runs[0], ctx

    @staticmethod
    def _result(run) -> dict:
        out: dict[str, Any] = {"ok": run.ok, "summary": run.summary}
        if run.ok is None:
            out["still_working"] = True
            out["note"] = "still running; its outcome is told to you when it lands. Do not say it is done."
            if run.progress:
                out["progress"] = run.progress
        if run.next:
            out["next"] = run.next
        if run.answer:
            out["answer"] = run.answer
        if run.outputs:
            out["outputs"] = [o.wire() for o in run.outputs][:8]
        if run.resource:
            out["resource"] = run.resource
        if run.ok is False and not run.summary:
            out["error"] = "it did not work"
        return out

    def _file(self, run, ctx) -> None:
        """Into the chat as the brain's steps are (a chat-action row by its action id), the same by voice as typed."""
        if self.dialogue is None:
            return
        with contextlib.suppress(Exception):
            self.dialogue.show({"type": "chat-action", "turnId": None, **run.wire(), "standalone": True}, asked=ctx.asked)
        if run.ok is not None:
            with contextlib.suppress(Exception):
                self.brain._remember_run_outputs(run, ctx, unbound=True)

    async def _late(self, run, ctx) -> None:
        """A step that finished after its call was answered "still working": its row and receipt, and one line. One
        that finished within the call's own wait was filed and answered already."""
        action = getattr(run.occurrence, "action_id", None)
        if action in self.reported:
            return
        self._file(run, ctx)
        if action:
            self.reported.add(action)
        status = "done" if run.ok else ("stopped" if run.stopped else "failed")
        text = json.dumps({"tool": run.tool, "status": status, "summary": run.summary,
                           **({"next": run.next} if run.next else {})}, ensure_ascii=False)
        await self.partner.happened("tool-done", text, ref=action or run.tool)

    @staticmethod
    def points_of(run) -> list[str]:
        """What a run put on screen, as mark targets: a layer of cell outlines, an outline."""
        out = []
        for output in run.outputs or []:
            wire = output.wire()
            if wire.get("kind") == "population" and wire.get("id"):
                out.append(f"population:{wire['id']}")
            elif wire.get("kind") == "outline" and wire.get("id"):
                out.append(f"outline:{wire['id']}")
        return out[:4]

    @staticmethod
    def _researcher_of(run) -> str | None:
        """The researcher a start_researcher run made, from its typed output (manager.attempt_ref)."""
        for output in run.outputs or []:
            ref = output.wire().get("resource")
            if isinstance(ref, dict) and ref.get("kind") == "research-run" and isinstance(ref.get("researcherId"), str):
                return ref["researcherId"]
        return None

    def _reported_at(self) -> float:
        screen = getattr(self.hub, "screen", None)
        try:
            return float(screen.reported_at) if screen is not None else 0.0
        except Exception:
            return 0.0

    async def look_after(self, name: str, before: float) -> tuple[dict, dict]:
        """Act, then look: the studio's report newer than the change, then its window picture and the screen as text."""
        screen = getattr(self.hub, "screen", None)
        report_at = None
        if screen is not None and hasattr(screen, "until"):
            with contextlib.suppress(Exception):
                if await screen.until(lambda report: True, before, timeout=REPORT_WAIT_S):
                    report_at = time.time()
        shot = await self.partner.snapshot()
        text = f"After {name}, Hooke's screen now: " + (self.partner.screen_now() or "(no report from the studio)")
        images = [(shot["jpeg"], f"Hooke's window after {name} ({shot.get('how') or 'composed'})")] if shot else []
        facts = {"screenChange": True, "reportT": report_at, "confirmed": report_at is not None}
        return {"images": images, "text": text}, facts

    # --- its own tools -----------------------------------------------------------------------------
    async def mark(self, args: dict) -> dict:
        """Draw marks in the studio; what it reports drawn decides what the model is told."""
        style = args.get("style") if args.get("style") in STYLES else "box"
        marks, frame = [], None
        for target in (args.get("targets") or [])[:6]:
            if isinstance(target, str):
                target = {"target": target}
            if not isinstance(target, dict) or not isinstance(target.get("target"), str):
                continue
            mark = {"target": target["target"][:120], "style": style}
            if args.get("label"):
                mark["label"] = str(args["label"])[:60]
            for key in ("region", "polygon"):
                if target["target"] == "frame" and isinstance(target.get(key), list):
                    mark[key] = target[key]
                    frame = self.partner.last_frame
            marks.append(mark)
        if not marks:
            return {"ok": False, "error": "no target to mark"}
        shown = await self.partner.marks(marks, frame=frame, timeout=MARKS_WAIT_S)
        if shown is None:
            return {"ok": False, "error": "the studio did not say it drew them; do not say they are shown",
                    "_log": {"marks": [m["target"] for m in marks]}}
        resolved = [r for r in shown.get("resolved") or [] if isinstance(r, dict)]
        drawn = [r.get("target") for r in resolved if r.get("ok")]
        missed = [{"target": r.get("target"), "why": r.get("why") or "not drawn"} for r in resolved if not r.get("ok")]
        return {"ok": bool(drawn), "shown": drawn, **({"not_shown": missed} if missed else {})}

    async def _bounded(self, name: str, work, call_id: str | None) -> dict:
        """A tool of its own with the registry tools' limit: past TOOL_WAIT_S it is answered "still working", and what
        it found is said as one line when it lands (a long tool done)."""
        task = asyncio.ensure_future(work)
        done, _ = await asyncio.wait({task}, timeout=TOOL_WAIT_S)
        if task in done:
            return task.result()

        def landed(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            if t.exception() is not None:
                found = {"tool": name, "status": "failed", "error": "something went wrong in the app"}
            else:
                found = {"tool": name, **{k: v for k, v in t.result().items() if not k.startswith("_")}}
            asyncio.ensure_future(self.partner.happened("tool-done", json.dumps(found, ensure_ascii=False, default=str),
                                                        ref=call_id or name))
        task.add_done_callback(landed)
        return {"ok": None, "still_working": True,
                "note": "still running; its outcome is told to you when it lands. Do not say it is done."}

    async def look_hard(self, args: dict) -> dict:
        return await self._bounded("look_hard", self._look_hard(args), args.get("_call_id"))

    async def _look_hard(self, args: dict) -> dict:
        """The frame (and the window, when asked) with the question, to the app's frontier vision model."""
        question = str(args.get("question") or "").strip()
        if not question:
            return {"ok": False, "error": "no question"}
        images = []
        seen = await self.partner.camera(1024)
        if seen is not None:
            images.append(seen[0])
        if args.get("window"):
            shot = await self.partner.snapshot()
            if shot:
                images.append(shot["jpeg"])
        if not images:
            return {"ok": False, "error": "no fresh camera frame and no window picture to look at"}
        option = getattr(self.hub.settings, "vision", None)
        try:
            vision = self.brain.agent.vision(option)
            result = await vision.ask(images, "Answer the question about the picture(s) as JSON {\"answer\": \"...\"}: "
                                      "one or two plain sentences, saying what you are unsure of.\nQuestion: " + question,
                                      max_output_tokens=600)
        except Exception as exc:  # a missing key or a provider error is said, plainly
            return {"ok": False, "error": f"the vision model could not look ({type(exc).__name__})"}
        if result.error:
            return {"ok": False, "error": result.error[:300]}
        answer = (result.data or {}).get("answer") if isinstance(result.data, dict) else None
        return {"ok": True, "answer": str(answer or result.raw_text or "")[:1500], "model": result.model}

    async def do_in_hooke(self, args: dict) -> dict:
        return await self._bounded("do_in_hooke", self._do_in_hooke(args), args.get("_call_id"))

    async def _do_in_hooke(self, args: dict) -> dict:
        """A many-step request handed to the brain whole, in the user's words; what it really did comes back (its
        failures said as failures), with a fresh look when it changed the screen."""
        text = str(args.get("request") or "").strip()
        if not text:
            return {"ok": False, "error": "no request"}
        from ..ticket import issue

        brain, hub = self.brain, self.hub
        kwargs: dict[str, Any] = {"voice_owner": self.partner.session}
        if self.client is not None:
            request = issue(hub, "turn", asked=brain.conversation.here(), channel="voice")
            try:
                pin = hub.screen.pin(self.client, request_id=request.admission.request_id, timing="server-handoff-arrival")
                kwargs.update(client=self.client, request_snapshot=pin, request_ticket=request, asked=request.asked)
            except Exception:
                request.seal()
        before = self._reported_at()
        result = await brain.turn(text, "voice", **kwargs)
        actions = [{"tool": a.get("tool"), "ok": a.get("ok"), "summary": a.get("summary")}
                   for a in (result.get("actions") or [])][:8]
        answer = str(result.get("answer") or "")[:1800]
        failed = [f"{a['tool']}: {a['summary']}" for a in actions if a["ok"] is False]
        working = [a["tool"] for a in actions if a["ok"] is None]
        out: dict[str, Any] = {"ok": None if working and not failed else (not failed and bool(answer or actions)),
                               "answer": answer, "actions": actions}
        if failed:
            out["failed"] = failed
        if result.get("error"):
            out["ok"], out["error"] = False, str(result["error"])[:300]
        changed = [a["tool"] for a in actions if a["ok"] and a["tool"] in SCREEN_CHANGING]
        if changed:  # act, then look: whether it shows is read from the screen, not from the brain's own answer
            look, facts = await self.look_after(changed[-1], before)
            out["_look"], out["_log"] = look, facts
        return out
