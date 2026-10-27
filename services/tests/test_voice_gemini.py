"""Gemini Live adapter against a fake connection (no network, no keys)."""
import asyncio
import contextlib

import pytest

types = pytest.importorskip("google.genai.types")

from orchestrator.voice.base import ToolSpec, VoiceConfig  # noqa: E402
from orchestrator.voice.gemini_live import (  # noqa: E402
    GeminiLiveSession,
    build_connect_config,
    supports_scheduling,
    translate,
)
from orchestrator.voice.scene_tools import wants_locate  # noqa: E402

SCHEMA = {"type": "object", "properties": {"description": {"type": "string"}}, "required": ["description"]}


class FakeLive:
    def __init__(self) -> None:
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.realtime: list[dict] = []
        self.client_content: list[dict] = []
        self.tool_responses: list[list] = []

    async def send_realtime_input(self, **kwargs) -> None:
        self.realtime.append(kwargs)

    async def send_client_content(self, **kwargs) -> None:
        self.client_content.append(kwargs)

    async def send_tool_response(self, *, function_responses) -> None:
        self.tool_responses.append(list(function_responses))

    async def receive(self):
        while True:
            msg = await self.inbox.get()
            if msg is None:
                return
            yield msg
            if msg.server_content is not None and msg.server_content.turn_complete:
                return


def fake_connect(fake: FakeLive, seen: dict):
    @contextlib.asynccontextmanager
    async def connect(*, model, config):
        seen["model"], seen["config"] = model, config
        yield fake

    return connect


def tool_call_msg(call_id="c1", name="locate", args=None):
    return types.LiveServerMessage(tool_call=types.LiveServerToolCall(
        function_calls=[types.FunctionCall(id=call_id, name=name, args=args or {"description": "dark dot"})]
    ))


def config(model="gemini-3.8-live", **kw):
    async def handler(args):
        await asyncio.sleep(0.01)
        return {"likely": "dust", "description": args["description"]}

    return VoiceConfig(model=model, system_prompt="sys", tools=[ToolSpec("locate", "find", SCHEMA, handler, **kw)])


async def collect_until(session, kind, timeout=2.0):
    seen = []

    async def run():
        async for event in session.events():
            seen.append(event)
            if event.kind == kind:
                return

    await asyncio.wait_for(run(), timeout)
    return seen


def test_connect_config():
    cfg = config()
    cfg.silence_ms = 300
    cfg.extra = {"thinking_budget": 0}
    live = build_connect_config(cfg)
    decl = live.tools[0].function_declarations[0]
    assert decl.behavior == types.Behavior.NON_BLOCKING
    assert live.realtime_input_config.automatic_activity_detection.silence_duration_ms == 300
    assert live.thinking_config.thinking_budget == 0
    assert live.context_window_compression.sliding_window is not None
    assert live.input_audio_transcription is not None and live.output_audio_transcription is not None

    cfg.client_turns = True
    assert build_connect_config(cfg).realtime_input_config.automatic_activity_detection.disabled is True


def test_scheduling_support():
    assert supports_scheduling(config())
    assert not supports_scheduling(config("gemini-3.8-live-extended-thinking"))


def test_translate_audio_transcripts_and_turns():
    msg = types.LiveServerMessage(server_content=types.LiveServerContent(
        model_turn=types.Content(role="model", parts=[types.Part(inline_data=types.Blob(data=b"\x01\x02" * 10, mime_type="audio/pcm;rate=24000"))]),
        output_transcription=types.Transcription(text="Let me look"),
        input_transcription=types.Transcription(text="what's that"),
        interim_input_transcription=types.Transcription(text="what's"),
        turn_complete=True,
    ))
    kinds = [(e.kind, e.text, e.rate, e.data.get("interim")) for e in translate(msg)]
    assert ("input_transcript", "what's", 0, True) in kinds
    assert ("input_transcript", "what's that", 0, None) in kinds
    assert ("audio", "", 24000, None) in kinds
    assert ("output_transcript", "Let me look", 0, None) in kinds
    assert kinds[-1][0] == "turn_complete"


def test_tool_call_runs_handler_and_answers_with_interrupt():
    async def main():
        fake, seen = FakeLive(), {}
        session = GeminiLiveSession(config(), connect=fake_connect(fake, seen))
        await session.start()
        await fake.inbox.put(tool_call_msg())
        events = await collect_until(session, "tool_result")
        await session.close()
        return fake, seen, events

    fake, seen, events = asyncio.run(main())
    assert seen["model"] == "gemini-3.8-live"
    assert [e.kind for e in events][-2:] == ["tool_call", "tool_result"]
    (response,) = fake.tool_responses[0]
    assert response.id == "c1" and response.name == "locate"
    assert response.response == {"likely": "dust", "description": "dark dot"}
    assert response.scheduling == types.FunctionResponseScheduling.INTERRUPT


def test_extended_thinking_gets_no_scheduling_and_interim_is_sent_first():
    async def main():
        fake = FakeLive()
        cfg = config("gemini-3.8-live-extended-thinking", interim={"status": "in_progress"})
        session = GeminiLiveSession(cfg, connect=fake_connect(fake, {}))
        await session.start()
        await fake.inbox.put(tool_call_msg())
        events = []
        while sum(e.kind == "tool_result" for e in events) < 2:
            events += await collect_until(session, "tool_result")
        await session.close()
        return fake

    fake = asyncio.run(main())
    first, final = fake.tool_responses[0][0], fake.tool_responses[1][0]
    assert first.will_continue is True and first.response == {"status": "in_progress"}
    assert final.will_continue is False
    assert first.scheduling is None and final.scheduling is None


def test_tool_errors_are_returned_to_the_model():
    async def boom(args):
        raise RuntimeError("camera stale")

    async def main():
        fake = FakeLive()
        cfg = VoiceConfig(model="gemini-3.8-live", tools=[ToolSpec("locate", "find", SCHEMA, boom)])
        session = GeminiLiveSession(cfg, connect=fake_connect(fake, {}))
        await session.start()
        await fake.inbox.put(tool_call_msg())
        await collect_until(session, "tool_result")
        await session.close()
        return fake

    fake = asyncio.run(main())
    assert "camera stale" in fake.tool_responses[0][0].response["error"]


def test_frames_are_rate_limited_and_inputs_forwarded():
    async def main():
        fake = FakeLive()
        session = GeminiLiveSession(config(), connect=fake_connect(fake, {}), max_fps=1.0)
        await session.start()
        sent = [await session.send_frame(b"jpeg1"), await session.send_frame(b"jpeg2")]
        await session.send_audio(b"\x00\x00" * 160)
        await session.inject("the user clicked the oval", respond=False)
        await session.close()
        return fake, sent

    fake, sent = asyncio.run(main())
    assert sent == [True, False]
    assert fake.realtime[0]["video"].mime_type == "image/jpeg"
    assert fake.realtime[1]["audio"].mime_type == "audio/pcm;rate=16000"
    assert fake.client_content[0]["turn_complete"] is False


def test_locate_intent():
    assert wants_locate("What's the little dark dot near the top?")
    assert wants_locate("highlight the nucleus")
    assert not wants_locate("thanks, that's great")
