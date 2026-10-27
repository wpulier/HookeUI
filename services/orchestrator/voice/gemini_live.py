"""Gemini Live adapter (`gemini-3.8-live`, `gemini-3.8-live-extended-thinking`).

One WebSocket session via google-genai's `client.aio.live.connect`. Microphone PCM (16 kHz)
and JPEG frames (at most `max_fps`, default 1) go in as realtime input; the server's
voice activity detection ends user turns. Output is 24 kHz PCM plus transcripts.

Tools are declared NON_BLOCKING by default: the model keeps talking ("let me look...")
while the handler runs, and the result is returned with a FunctionResponse `scheduling`
(INTERRUPT by default, so the model speaks about it straight away). See
docs/bench/live.md for measured latencies and why these defaults were chosen.

Session length: context-window compression (sliding window) is on by default so long
audio+video sessions are not cut off; session-resumption handles are tracked so
`reconnect()` can continue a conversation after a `go_away`.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import time
from typing import Any, AsyncIterator, Callable

from .base import ToolSpec, VoiceConfig, VoiceEvent, VoiceSession

_SCHEDULING = {"interrupt": "INTERRUPT", "when_idle": "WHEN_IDLE", "silent": "SILENT"}


def build_connect_config(config: VoiceConfig, resume_handle: str | None = None):
    """VoiceConfig -> google.genai LiveConnectConfig. Kept separate so it can be unit tested
    and inspected without opening a connection.

    Recognised `config.extra` keys: thinking_level ("minimal".."high"), thinking_budget (int),
    include_thoughts (bool), compression (bool, default True), media_resolution
    ("low"|"medium"|"high"), proactive_audio (bool), affective_dialog (bool),
    start_sensitivity / end_sensitivity ("high"|"low"), prefix_padding_ms (int)."""
    from google.genai import types

    extra = config.extra
    declarations = []
    for spec in config.tools:
        declarations.append(types.FunctionDeclaration(
            name=spec.name,
            description=spec.description,
            parameters_json_schema=spec.parameters,
            behavior=types.Behavior.NON_BLOCKING if spec.non_blocking else types.Behavior.BLOCKING,
        ))
    live = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=config.system_prompt or None,
        tools=[types.Tool(function_declarations=declarations)] if declarations else None,
        input_audio_transcription=types.AudioTranscriptionConfig() if config.transcribe_input else None,
        output_audio_transcription=types.AudioTranscriptionConfig() if config.transcribe_output else None,
        session_resumption=types.SessionResumptionConfig(handle=resume_handle),
    )
    if config.voice:
        live.speech_config = types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=config.voice))
        )
    if extra.get("compression", True):
        live.context_window_compression = types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow())
    thinking = {}
    if extra.get("thinking_level"):
        thinking["thinking_level"] = getattr(types.ThinkingLevel, str(extra["thinking_level"]).upper())
    if extra.get("thinking_budget") is not None:
        thinking["thinking_budget"] = int(extra["thinking_budget"])
    if extra.get("include_thoughts") is not None:
        thinking["include_thoughts"] = bool(extra["include_thoughts"])
    if thinking:
        live.thinking_config = types.ThinkingConfig(**thinking)
    if extra.get("media_resolution"):
        live.media_resolution = getattr(types.MediaResolution, f"MEDIA_RESOLUTION_{str(extra['media_resolution']).upper()}")
    if extra.get("proactive_audio") is not None:
        live.proactivity = types.ProactivityConfig(proactive_audio=bool(extra["proactive_audio"]))
    if extra.get("affective_dialog") is not None:
        live.enable_affective_dialog = bool(extra["affective_dialog"])
    vad: dict[str, Any] = {}
    if config.silence_ms is not None:
        vad["silence_duration_ms"] = int(config.silence_ms)
    if extra.get("prefix_padding_ms") is not None:
        vad["prefix_padding_ms"] = int(extra["prefix_padding_ms"])
    if extra.get("start_sensitivity"):
        vad["start_of_speech_sensitivity"] = getattr(types.StartSensitivity, f"START_SENSITIVITY_{extra['start_sensitivity'].upper()}")
    if extra.get("end_sensitivity"):
        vad["end_of_speech_sensitivity"] = getattr(types.EndSensitivity, f"END_SENSITIVITY_{extra['end_sensitivity'].upper()}")
    if config.client_turns:
        live.realtime_input_config = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
        )
    elif vad:
        live.realtime_input_config = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(**vad)
        )
    return live


def supports_scheduling(config: VoiceConfig) -> bool:
    """gemini-3.8-live-extended-thinking rejects FunctionResponse.scheduling (API error 1007,
    which ends the session), so results go back without it there."""
    if "function_response_scheduling" in config.extra:
        return bool(config.extra["function_response_scheduling"])
    return "extended-thinking" not in config.model


def translate(msg) -> list[VoiceEvent]:
    """One google.genai LiveServerMessage -> neutral events (tool calls included, but not
    executed here). Pure, so it is unit-testable with constructed messages."""
    events: list[VoiceEvent] = []
    now = time.monotonic()
    if msg.setup_complete is not None:
        events.append(VoiceEvent("ready", now, data={"session_id": msg.setup_complete.session_id}))
    if msg.voice_activity is not None and msg.voice_activity.voice_activity_type is not None:
        state = "start" if msg.voice_activity.voice_activity_type.name.endswith("START") else "end"
        events.append(VoiceEvent("vad", now, data={"state": state, "source": "voice_activity"}))
    if msg.voice_activity_detection_signal is not None and msg.voice_activity_detection_signal.vad_signal_type is not None:
        name = msg.voice_activity_detection_signal.vad_signal_type.name
        if name.endswith(("SOS", "EOS")):
            events.append(VoiceEvent("vad", now, data={"state": "start" if name.endswith("SOS") else "end", "source": "vad_signal"}))
    sc = msg.server_content
    if sc is not None:
        if sc.interim_input_transcription is not None and sc.interim_input_transcription.text:
            # Low-latency transcript while the user is still speaking (replaced by the next one).
            events.append(VoiceEvent("input_transcript", now, text=sc.interim_input_transcription.text, data={"interim": True}))
        if sc.input_transcription is not None and sc.input_transcription.text:
            events.append(VoiceEvent("input_transcript", now, text=sc.input_transcription.text))
        if sc.model_turn is not None:
            for part in sc.model_turn.parts or []:
                blob = part.inline_data
                if blob is not None and blob.data and (blob.mime_type or "").startswith("audio/"):
                    rate = 24000
                    if "rate=" in (blob.mime_type or ""):
                        rate = int(blob.mime_type.split("rate=")[1].split(";")[0])
                    events.append(VoiceEvent("audio", now, audio=blob.data, rate=rate))
                elif part.text and part.thought:
                    events.append(VoiceEvent("output_transcript", now, text=part.text, data={"thought": True}))
        if sc.output_transcription is not None and sc.output_transcription.text:
            events.append(VoiceEvent("output_transcript", now, text=sc.output_transcription.text))
        if sc.interrupted:
            events.append(VoiceEvent("interrupted", now))
        if sc.generation_complete:
            events.append(VoiceEvent("generation_complete", now))
        if sc.turn_complete:
            reason = sc.turn_complete_reason.name if sc.turn_complete_reason else None
            events.append(VoiceEvent("turn_complete", now, data={"reason": reason}))
    if msg.tool_call is not None:
        for fc in msg.tool_call.function_calls or []:
            events.append(VoiceEvent("tool_call", now, call_id=fc.id or "", name=fc.name or "", args=dict(fc.args or {})))
    if msg.tool_call_cancellation is not None:
        for call_id in msg.tool_call_cancellation.ids or []:
            events.append(VoiceEvent("tool_cancelled", now, call_id=call_id))
    if msg.usage_metadata is not None:
        u = msg.usage_metadata
        events.append(VoiceEvent("usage", now, data={
            "prompt": u.prompt_token_count, "response": u.response_token_count,
            "thoughts": u.thoughts_token_count, "total": u.total_token_count,
        }))
    if msg.session_resumption_update is not None and msg.session_resumption_update.new_handle:
        events.append(VoiceEvent("resumption", now, data={"handle": msg.session_resumption_update.new_handle}))
    if msg.go_away is not None:
        events.append(VoiceEvent("go_away", now, data={"time_left": str(msg.go_away.time_left)}))
    return events


class GeminiLiveSession(VoiceSession):
    def __init__(
        self,
        config: VoiceConfig,
        *,
        api_key: str | None = None,
        connect: Callable[..., Any] | None = None,
        max_fps: float = 1.0,
    ) -> None:
        """`connect(model=..., config=...)` returns an async context manager yielding a
        live session; defaults to google-genai's client.aio.live.connect. Tests pass a fake."""
        self.config = config
        self.max_fps = max_fps
        self._connect = connect
        self._api_key = api_key
        self._session = None
        self._cm = None
        self._queue: asyncio.Queue[VoiceEvent] = asyncio.Queue()
        self._recv_task: asyncio.Task | None = None
        self._tool_tasks: dict[str, asyncio.Task] = {}
        self._tools: dict[str, ToolSpec] = {t.name: t for t in config.tools}
        self._last_frame_at = 0.0
        self.resume_handle: str | None = None
        self.closed = False

    # --- lifecycle -------------------------------------------------------------------
    async def start(self) -> None:
        connect = self._connect
        if connect is None:
            from google import genai

            client = genai.Client(api_key=self._api_key or os.environ["GEMINI_API_KEY"])
            connect = client.aio.live.connect
        self._cm = connect(model=self.config.model, config=build_connect_config(self.config, self.resume_handle))
        self._session = await self._cm.__aenter__()
        self.closed = False
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def close(self) -> None:
        await self._shutdown()
        self._emit(VoiceEvent("closed", data={"reason": "client"}))

    async def reconnect(self) -> None:
        """Re-open with the latest resumption handle (after `go_away`, or a dropped socket);
        the conversation continues where it was."""
        await self._shutdown()
        await self.start()

    async def _shutdown(self) -> None:
        self.closed = True
        for task in list(self._tool_tasks.values()):
            task.cancel()
        self._tool_tasks.clear()
        if self._recv_task is not None:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._recv_task
            self._recv_task = None
        if self._cm is not None:
            with contextlib.suppress(Exception):
                await self._cm.__aexit__(None, None, None)
            self._cm = self._session = None

    # --- input -----------------------------------------------------------------------
    async def send_audio(self, pcm: bytes) -> None:
        from google.genai import types

        if self._session is None or self.closed:
            return
        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={self.config.input_rate}")
        )

    async def send_frame(self, jpeg: bytes, *, force: bool = False) -> bool:
        """Returns False if the frame was dropped to respect max_fps."""
        from google.genai import types

        if self._session is None or self.closed:
            return False
        now = time.monotonic()
        if not force and now - self._last_frame_at < 1.0 / self.max_fps - 0.02:
            return False
        self._last_frame_at = now
        await self._session.send_realtime_input(video=types.Blob(data=jpeg, mime_type="image/jpeg"))
        return True

    async def send_text(self, text: str) -> None:
        if self._session is None or self.closed:
            return
        await self._session.send_realtime_input(text=text)

    async def begin_user_turn(self) -> None:
        from google.genai import types

        if self._session is not None and not self.closed:
            await self._session.send_realtime_input(activity_start=types.ActivityStart())

    async def end_user_turn(self) -> None:
        from google.genai import types

        if self._session is not None and not self.closed:
            await self._session.send_realtime_input(activity_end=types.ActivityEnd())

    async def inject(self, text: str, *, respond: bool = False, delegation_id: str | None = None,
                     source: str = "app", facts: dict | None = None) -> None:
        from google.genai import types

        if self._session is None or self.closed:
            return
        await self._session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)]), turn_complete=respond
        )

    # --- output ----------------------------------------------------------------------
    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            event = await self._queue.get()
            yield event
            if event.kind == "closed":
                return

    def _emit(self, event: VoiceEvent) -> None:
        self._queue.put_nowait(event)
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception:  # a broken listener must not kill the session
                pass

    async def _receive_loop(self) -> None:
        reason = "server"
        try:
            while not self.closed:
                got = False
                async for msg in self._session.receive():
                    got = True
                    for event in translate(msg):
                        if event.kind == "resumption":
                            self.resume_handle = event.data["handle"]
                        self._emit(event)
                        if event.kind == "tool_call":
                            self._start_tool(event)
                        elif event.kind == "tool_cancelled":
                            task = self._tool_tasks.pop(event.call_id, None)
                            if task is not None:
                                task.cancel()
                if not got:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"[:300]
            self._emit(VoiceEvent("error", data={"message": reason}))
        if not self.closed:
            self.closed = True
            self._emit(VoiceEvent("closed", data={"reason": reason}))

    # --- tools -----------------------------------------------------------------------
    def _start_tool(self, call: VoiceEvent) -> None:
        self._tool_tasks[call.call_id] = asyncio.create_task(self._run_tool(call))

    async def _run_tool(self, call: VoiceEvent) -> None:
        from google.genai import types

        spec = self._tools.get(call.name)
        t0 = time.perf_counter()
        if spec is not None and spec.interim is not None and spec.non_blocking and self._session is not None:
            interim = types.FunctionResponse(id=call.call_id, name=call.name, response=spec.interim, will_continue=True)
            if supports_scheduling(self.config):
                interim.scheduling = getattr(types.FunctionResponseScheduling, _SCHEDULING[spec.interim_scheduling])
            try:
                await self._session.send_tool_response(function_responses=[interim])
                self._emit(VoiceEvent("tool_result", call_id=call.call_id, name=call.name, result=spec.interim, data={"ms": 0, "interim": True}))
            except Exception as exc:
                self._emit(VoiceEvent("error", data={"message": f"interim tool response failed: {type(exc).__name__}: {exc}"[:300]}))
        if spec is None:
            result: dict[str, Any] = {"error": f"unknown tool {call.name}"}
        else:
            try:
                result = await spec.handler(call.args)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                result = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        ms = (time.perf_counter() - t0) * 1000
        self._tool_tasks.pop(call.call_id, None)
        if self._session is None or self.closed:
            return
        response = types.FunctionResponse(id=call.call_id, name=call.name, response=result)
        if spec is not None and spec.interim is not None and spec.non_blocking:
            response.will_continue = False
        if (spec is None or spec.non_blocking) and supports_scheduling(self.config):
            scheduling = _SCHEDULING[spec.scheduling if spec else "when_idle"]
            response.scheduling = getattr(types.FunctionResponseScheduling, scheduling)
        try:
            await self._session.send_tool_response(function_responses=[response])
        except Exception as exc:
            self._emit(VoiceEvent("error", data={"message": f"tool response failed: {type(exc).__name__}: {exc}"[:300]}))
            return
        self._emit(VoiceEvent("tool_result", call_id=call.call_id, name=call.name, result=result, data={"ms": round(ms)}))
