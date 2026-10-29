"""`think`, the frontier mind behind the one-brain voice (voice/think.py, partner_tools.think, RealtimeSession.deliver),
against a fake backend and the fake sideband of test_voice_partner."""
import asyncio
import base64
import json
import time

import httpx

from orchestrator.voice import openai_realtime, think as thinking
from tests.test_voice_partner import done, partner_session, press, release, settle  # noqa: F401  (fixtures too)
from tests.test_voice_partner import no_tail  # noqa: F401  (autouse: no release tail, no progress lines)
from tests.waiting import until_async


class FakeBackend:
    """A frontier model in a box: calls the tools it is given, then reports."""

    def __init__(self, steps=(), report=None, refuse=None, wait=None):
        self.steps, self.report, self.refuse, self.wait = list(steps), report or {}, refuse, wait
        self.model = "fake-opus"
        self.tasks, self.seen = [], []

    async def run(self, task, tools, call_tool, progress, system):
        self.tasks.append(task)
        if self.wait is not None:
            await self.wait.wait()
        result = thinking.ThinkResult("", None, model=self.model)
        if self.refuse:
            result.refused, result.ok = self.refuse, False
            return result
        for name, args in self.steps:
            progress(name)
            out = await call_tool(name, args)
            self.seen.append((name, out))
            result.tools.append(thinking.summary_of(name, out))
        return thinking.finish(result, self.report)


async def with_backend(monkeypatch, backend, tools=None):
    t = await partner_session(monkeypatch, tools=tools)
    t.partner.tools.backend = lambda option: backend
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # the option's key is present (its value never used here)
    return t


async def think_call(t, request="compare three outline models and report", call_id="c_think", rid="resp_1",
                     spoken=""):
    if spoken:
        t.sb.emit({"type": "response.output_audio_transcript.delta", "response_id": rid, "delta": spoken})
    t.sb.emit({"type": "response.function_call_arguments.done", "response_id": rid, "call_id": call_id,
               "name": "think", "arguments": json.dumps({"request": request, "why": "several steps"})})
    await done(t.sb, rid)


def creates(t):
    return [e for e in t.sb.sent if e["type"] == "response.create"]


def test_think_starts_at_once_and_its_result_is_said_once_with_marks(monkeypatch):
    """Architecture item 2: "started" within 200 ms; the backend works behind; its result is prepared out of band
    (conversation none, text only, the task in metadata), added to the conversation, said once through the gate, and
    what it names is pointed at as the words start."""
    gate = asyncio.Event()

    async def go():
        backend = FakeBackend(report={"say": "MicroSAM was closest to Cellpose.", "points": ["population:e2"],
                                      "checked": True, "done": True}, wait=gate)
        t = await with_backend(monkeypatch, backend)
        spec = next(s for s in t.session.config.tools if s.name == "think")
        t0 = time.perf_counter()
        started = await spec.handler({"request": "compare three outline models", "why": "many steps", "_call_id": "x"})
        assert time.perf_counter() - t0 < 0.2 and started["started"] is True and started["task"]
        gate.set()
        prep = await until_async(lambda: next((e for e in creates(t) if e["response"].get("conversation") == "none"), None))
        assert prep["response"]["output_modalities"] == ["text"] and prep["response"]["metadata"]["task"] == started["task"]
        assert prep["response"]["metadata"]["cause"] == "prep"
        t.sb.emit({"type": "response.output_text.delta", "response_id": "resp_1", "delta": "MicroSAM matched Cellpose best."})
        await done(t.sb, "resp_1")
        said = await until_async(lambda: next((e for e in creates(t) if e["response"]["metadata"]["cause"] == "think"), None))
        assert "MicroSAM matched Cellpose best." in said["response"]["instructions"]
        result = [i for i in t.sb.items() if i.get("role") == "system" and "Result of the task" in i["content"][0]["text"]]
        assert len(result) == 1 and "MicroSAM was closest" in result[0]["content"][0]["text"]
        assert not [turn for turn in t.brain.conversation.turns if "MicroSAM matched" in turn.text], \
            "the out-of-band words are never the chat's line"
        t.sb.emit({"type": "output_audio_buffer.started", "response_id": "resp_2"})
        marks = await until_async(lambda: next((m for m in t.studio if m["type"] == "marks"), None))
        assert marks["marks"] == [{"target": "population:e2", "style": "box"}]
        await done(t.sb, "resp_2")
        await t.session.deliver(started["task"], facts={}, say="again", points=[])  # a second delivery of it
        await asyncio.sleep(0.3)
        assert len([e for e in creates(t) if e["response"]["metadata"]["cause"] == "think"]) == 1, "said exactly once"
        record = next(r for r in t.logged if r["kind"] == "partner-think")
        assert record["ok"] is True and record["checked"] is True and record["model"] == "fake-opus"
    asyncio.run(go())


def test_one_acknowledgement_only(monkeypatch):
    """HeyClicky's handoff bug: the acknowledgement said twice. Spoken before the call: nothing more is asked for. Not
    spoken: one short acknowledgement, never a second answer."""
    gate = asyncio.Event()

    async def go():
        t = await with_backend(monkeypatch, FakeBackend(wait=gate, report={"say": "Done.", "checked": True, "done": True}))
        await press(t, 1)
        await release(t, 1)
        await think_call(t, spoken="On it.")
        await until_async(lambda: any(i["type"] == "function_call_output" for i in t.sb.items()))
        await asyncio.sleep(0.3)
        assert [e["response"]["metadata"]["cause"] for e in creates(t)] == ["turn"], "no second acknowledgement"
        await press(t, 2)
        await release(t, 2)
        await think_call(t, call_id="c2", rid="resp_2")
        ack = await until_async(lambda: next((e for e in creates(t) if e["response"]["metadata"]["cause"] == "ack"), None))
        assert ack["response"]["tool_choice"] == "none" and ack["response"]["max_output_tokens"] <= 60
        await asyncio.sleep(0.3)
        assert [e["response"]["metadata"]["cause"] for e in creates(t)] == ["turn", "turn", "ack"]
    asyncio.run(go())


def test_thinks_backend_acts_through_the_registry_and_looks_again(monkeypatch):
    """The backend's calls are the voice's own path: a voice request each, attributed to the Assistant, and a change on
    screen comes back with the studio's report and a fresh window picture before anything is reported done."""
    async def go():
        backend = FakeBackend(steps=[("show_measurements", {"tab": "Sizes"})],
                              report={"say": "The table is open in Sizes.", "checked": True, "done": True})
        t = await with_backend(monkeypatch, backend, tools={"show_measurements": "the table is in front on Tab Sizes"})
        t.hub.screen.report(["w-1: live view"])
        await press(t, 1)
        await release(t, 1)
        spec = next(s for s in t.session.config.tools if s.name == "think")
        await spec.handler({"request": "outline them and put the sizes in a tab"})
        request = await until_async(lambda: next((m for m in t.studio if m["type"] == "snapshot-request"), None))
        await t.partner.command({"type": "snapshot", "id": request["id"], "jpeg": base64.b64encode(b"AFTER").decode()})
        await until_async(lambda: backend.seen)
        name, out = backend.seen[0]
        _, _, ctx = t.ran[0]
        assert name == "show_measurements" and ctx.channel == "voice" and ctx.voice_owner is t.session
        assert [jpeg for jpeg, _ in thinking.tool_pictures(out)] == [b"AFTER"]
        assert "screen_after" in thinking.tool_text(out)
        task = backend.tasks[0]
        assert [label.split(",")[0] for _, label in task.pictures][:1] == ["Camera"] and task.brief.startswith("Project Leaf")
    asyncio.run(go())


def test_think_waits_for_a_long_tools_real_outcome_and_it_is_never_said_on_its_own(monkeypatch):
    """think-broad-1 (2026-10-29): the backend's compare_models was still running at the voice's 12 s, so think
    reported "they're still running" and the comparison itself came ~40 s later as a separate tool-done line, during
    another step. Behind the voice, think waits for the outcome; it reaches the backend, and is said only in think's
    result, never as a line of its own."""
    monkeypatch.setattr("orchestrator.voice.partner_tools.TOOL_WAIT_S", 0.05)
    monkeypatch.setattr(openai_realtime, "PROGRESS_AFTER_S", 60.0)
    gate = asyncio.Event()

    async def comparing(ctx, args):
        await gate.wait()
        return "Cellpose-SAM 342, micro-sam 309 (300 matched), InstanSeg 41"

    async def go():
        backend = FakeBackend(steps=[("compare_models", {"what": "outline"})],
                              report={"say": "Cellpose-SAM and micro-sam agree.", "checked": True, "done": True})
        t = await with_backend(monkeypatch, backend, tools={"compare_models": comparing})
        t.brain.registry.tools["compare_models"].inline_ms = 20
        spec = next(s for s in t.session.config.tools if s.name == "think")
        await spec.handler({"request": "run three outline models and compare them"})
        await until_async(lambda: t.ran)
        await asyncio.sleep(0.3)  # well past the voice's own wait: the backend is still waiting for the outcome
        assert not backend.seen
        gate.set()
        await until_async(lambda: backend.seen)
        name, out = backend.seen[0]
        assert out["ok"] is True and "300 matched" in out["summary"] and "still_working" not in out
        await until_async(lambda: next((e for e in creates(t) if e["response"].get("conversation") == "none"), None))
        await asyncio.sleep(0.2)
        assert not [i for i in t.sb.items() if i.get("role") == "system" and "Something happened" in i["content"][0]["text"]], \
            "the comparison is think's to say, not a line of its own"
    asyncio.run(go())


def test_while_think_works_a_quiet_voice_says_what_is_under_way_a_few_times_and_never_after_its_result(monkeypatch):
    """think-broad-1: think's call returns "started" at once, so no tool round was pending and nothing was said for the
    20-40 s it worked (the standard: progress spoken). Behind a quiet voice, a short line on what is under way, at
    most THINK_PROGRESS_LINES, none while the user holds the key, none once the result is due."""
    monkeypatch.setattr(openai_realtime, "THINK_PROGRESS_AFTER_S", 0.2)
    monkeypatch.setattr(openai_realtime, "THINK_PROGRESS_EVERY_S", 0.3)
    monkeypatch.setattr(openai_realtime, "THINK_PROGRESS_LINES", 2)
    gate = asyncio.Event()

    async def go():
        backend = FakeBackend(report={"say": "Done comparing.", "checked": True, "done": True}, wait=gate)
        t = await with_backend(monkeypatch, backend)
        spec = next(s for s in t.session.config.tools if s.name == "think")
        await spec.handler({"request": "compare three outline models"})

        def progress():
            return [e for e in creates(t) if e["response"]["metadata"]["cause"] == "progress"]
        first = await until_async(lambda: progress()[0] if progress() else None)
        assert first["response"]["tool_choice"] == "none" and "thinking" in first["response"]["instructions"]
        await done(t.sb, "resp_1")
        await until_async(lambda: len(progress()) == 2)
        await done(t.sb, "resp_2")
        await asyncio.sleep(0.8)
        assert len(progress()) == 2, "a few lines, not a running commentary"
        gate.set()
        await until_async(lambda: next((e for e in creates(t) if e["response"].get("conversation") == "none"), None))
        assert len(progress()) == 2
    asyncio.run(go())


def test_a_tool_think_gave_up_waiting_for_is_still_said_when_it_lands(monkeypatch):
    """Past think's own wait the outcome is not lost: the backend is told it is still running, and when it lands it is
    said as one line, as any long tool's is."""
    monkeypatch.setattr("orchestrator.voice.partner_tools.TOOL_WAIT_S", 0.05)
    monkeypatch.setattr("orchestrator.voice.partner_tools.THINK_TOOL_WAIT_S", 0.1)
    monkeypatch.setattr(openai_realtime, "PROGRESS_AFTER_S", 60.0)
    gate = asyncio.Event()

    async def comparing(ctx, args):
        await gate.wait()
        return "Cellpose-SAM 342, micro-sam 309"

    async def go():
        backend = FakeBackend(steps=[("compare_models", {"what": "outline"})],
                              report={"say": "The comparison is still running.", "checked": False, "done": False})
        t = await with_backend(monkeypatch, backend, tools={"compare_models": comparing})
        t.brain.registry.tools["compare_models"].inline_ms = 20
        spec = next(s for s in t.session.config.tools if s.name == "think")
        await spec.handler({"request": "compare them"})
        await until_async(lambda: backend.seen)
        assert backend.seen[0][1]["still_working"] is True
        prep = await until_async(lambda: next((e for e in creates(t) if e["response"].get("conversation") == "none"), None))
        await done(t.sb, "resp_1")
        await until_async(lambda: next((e for e in creates(t) if e["response"]["metadata"]["cause"] == "think"), None))
        await done(t.sb, "resp_2")
        t.session._last_user_at = 0.0
        gate.set()
        await until_async(lambda: [i for i in t.sb.items() if i.get("role") == "system"
                                   and "Something happened" in i["content"][0]["text"]])
    asyncio.run(go())


def test_a_refusal_is_said_as_a_refusal_with_the_other_model_offered(monkeypatch):
    """Anthropic's safeguards can refuse life-science requests: said as that, the other backend offered by name; never
    answered silently by another model."""
    async def go():
        backend = FakeBackend(refuse="bio")
        t = await with_backend(monkeypatch, backend)
        spec = next(s for s in t.session.config.tools if s.name == "think")
        await spec.handler({"request": "how would this pathogen spread"})
        prep = await until_async(lambda: next((e for e in creates(t) if e["response"].get("conversation") == "none"), None))
        facts = prep["response"]["input"][0]["content"][0]["text"]
        assert "refused" in facts and "GPT-6.1 Sol" in facts
        record = next(r for r in t.logged if r["kind"] == "partner-think")
        assert record["refused"] == "bio" and record["ok"] is False
    asyncio.run(go())


def test_a_result_landing_while_the_user_talks_waits_and_is_never_over_them(monkeypatch):
    """The epoch: nothing from before a key press plays during it. A think result that lands while the key is held is
    said after the user's turn, once."""
    gate = asyncio.Event()

    async def go():
        t = await with_backend(monkeypatch, FakeBackend(wait=gate, report={"say": "Two groups.", "checked": True,
                                                                           "done": True}))
        spec = next(s for s in t.session.config.tools if s.name == "think")
        await spec.handler({"request": "group them by look"})
        await press(t, 1)  # the user starts talking
        gate.set()
        await asyncio.sleep(0.4)
        assert not creates(t), "not while the key is held"
        await release(t, 1)
        await done(t.sb, "resp_1")
        t.session._last_user_at = 0.0
        prep = await until_async(lambda: next((e for e in creates(t) if e["response"].get("conversation") == "none"), None))
        await done(t.sb, "resp_2")
        await until_async(lambda: any(e["response"]["metadata"]["cause"] == "think" for e in creates(t)), seconds=10)
        assert prep is not None
    asyncio.run(go())


def test_think_without_its_key_says_so_plainly(monkeypatch):
    async def go():
        t = await with_backend(monkeypatch, FakeBackend())
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        spec = next(s for s in t.session.config.tools if s.name == "think")
        await spec.handler({"request": "compare models"})
        prep = await until_async(lambda: next((e for e in creates(t) if e["response"].get("conversation") == "none"), None))
        assert "ANTHROPIC_API_KEY" in prep["response"]["input"][0]["content"][0]["text"]
    asyncio.run(go())


def test_two_researchers_declared_distinct_start_two_and_a_repeat_starts_none(monkeypatch):
    """The broad journey: "Make two researchers, one compares the sizes, the other looks for dividing cells." The model
    declares the second distinct (distinct_from: the other's brief); an accidental repeat still starts nothing new."""
    async def go():
        t = await partner_session(monkeypatch, tools={"start_researcher": "Researcher started"})
        spec = next(s for s in t.session.config.tools if s.name == "start_researcher")
        assert "distinct_from" in spec.parameters["properties"]
        await press(t, 1)
        await release(t, 1)
        t.sb.emit({"type": "conversation.item.input_audio_transcription.completed", "item_id": "a1",
                   "transcript": "make two researchers"})
        a = {"what": "compare the sizes"}
        b = {"what": "look for dividing cells", "distinct_from": "compare the sizes"}
        results = await asyncio.gather(spec.handler(dict(a)), spec.handler(dict(b)), spec.handler(dict(a)))
        assert [r[0] for r in t.ran] == ["start_researcher", "start_researcher"]
        assert "not done again" in results[2]["note"]
        assert "distinct_from" not in t.ran[1][1], "the registry tool gets its own arguments only"
    asyncio.run(go())


def test_switching_a_model_is_a_quick_tool_of_the_voice(monkeypatch):
    async def go():
        t = await partner_session(monkeypatch, tools={"use": "using Claude for seeing", "set_default": "Claude from now on"})
        assert {"use", "set_default", "think"} <= {s.name for s in t.session.config.tools}
        assert "do_in_hooke" not in {s.name for s in t.session.config.tools}
    asyncio.run(go())


# --- the Claude client (httpx, no SDK) ---------------------------------------------------------------------------
def test_the_claude_backend_runs_its_tool_loop_with_pictures_and_reports():
    replies = [
        {"stop_reason": "tool_use", "usage": {"input_tokens": 100, "output_tokens": 20}, "content": [
            {"type": "thinking", "thinking": "...", "signature": "sig"},
            {"type": "tool_use", "id": "tu1", "name": "outline_cells", "input": {}}]},
        {"stop_reason": "tool_use", "usage": {"input_tokens": 200, "output_tokens": 30}, "content": [
            {"type": "tool_use", "id": "tu2", "name": "report",
             "input": {"say": "344 nuclei outlined.", "points": ["population:e1"], "checked": True, "done": True}}]},
    ]
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=replies[len(bodies) - 1])

    async def go():
        client = httpx.AsyncClient(base_url="https://api.anthropic.com", transport=httpx.MockTransport(handler))
        backend = thinking.ClaudeThinker(client=client)

        async def call_tool(name, args):
            return {"ok": True, "summary": "outlined 344", "_look": {"images": [(b"WIN", "Hooke's window after")],
                                                                    "text": "screen now"}}
        task = thinking.ThinkTask("outline", pictures=[(b"CAM", "Camera, frame 1")], brief="Project Leaf")
        result = await thinking.think(task, backend, [{"name": "outline_cells", "description": "d",
                                                        "parameters": {"type": "object"}}], call_tool, lambda s: None,
                                      "system")
        assert result.ok is True and result.say == "344 nuclei outlined." and result.points == ["population:e1"]
        first, second = bodies
        assert first["model"] == "claude-opus-5-5" and first["output_config"] == {"effort": "low"}
        assert first["messages"][0]["content"][1]["type"] == "image"
        assert {t["name"] for t in first["tools"]} == {"outline_cells", "report"}
        # Cached: the tools and the system prompt (the same for every task) up to a marker on the system, and each
        # round's growing conversation by the automatic marker, so a second round reads its first back.
        assert first["system"] == [{"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}]
        assert first["cache_control"] == {"type": "ephemeral"}
        assert second["messages"][1]["content"][0]["type"] == "thinking", "its thinking goes back whole"
        answer = second["messages"][2]["content"][0]
        assert answer["type"] == "tool_result" and answer["content"][-1]["type"] == "image"
    asyncio.run(go())


def test_a_claude_refusal_comes_back_as_a_refusal():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"stop_reason": "refusal", "stop_details": {"category": "bio"}, "content": [],
                                         "usage": {"input_tokens": 10, "output_tokens": 0}})

    async def go():
        client = httpx.AsyncClient(base_url="https://api.anthropic.com", transport=httpx.MockTransport(handler))
        result = await thinking.think(thinking.ThinkTask("q"), thinking.ClaudeThinker(client=client), [],
                                      lambda n, a: None, lambda s: None, "system")
        assert result.refused == "bio" and result.ok is False
    asyncio.run(go())
