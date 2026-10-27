"""Provider-neutral voice session.

A voice session is one realtime conversation with a speech-to-speech model. The orchestrator
pushes microphone audio and (if the provider takes it) video frames in, and reads a single
stream of `VoiceEvent`s out: output audio, transcripts, turn boundaries and tool activity.

Tools are declared once as `ToolSpec`s. The session calls the handler itself when the model
asks for a tool and returns the result to the model, so every provider (Gemini Live,
OpenAI GPT-Live, gpt-realtime) drives the same scene tools the same way. Tool calls and
results also appear on the event stream so callers can time and display them.

Audio formats: input is 16-bit little-endian mono PCM at `input_rate` (16 kHz for Gemini);
output audio events carry their own sample rate (24 kHz for Gemini).
"""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Literal

EventKind = Literal[
    "ready",              # session is open; data may carry a provider session id
    "audio",              # output speech chunk: audio=bytes, rate=int
    "input_transcript",   # what the user said: text; data={"interim": True} for a running
                          # transcript while they speak (each replaces the previous interim)
    "output_transcript",  # what the model is saying (pieces, in order): text
    "vad",                # server voice activity: data={"state": "start"|"end"}
    "interrupted",        # the model stopped speaking (user barged in, or an INTERRUPT result)
    "generation_complete",  # the model finished generating this turn (audio may still be playing)
    "turn_complete",      # the turn is over; data may carry {"reason": ...}
    "tool_call",          # model asked for a tool: call_id, name, args
    "tool_result",        # we answered it: call_id, name, result, data={"ms": handler time}
    "tool_cancelled",     # the model withdrew a pending call: call_id
    "usage",              # token usage: data
    "resumption",         # new session-resumption handle: data={"handle": str}
    "go_away",            # server will close soon: data={"time_left": str}
    "error",              # data={"message": str}
    "closed",             # the session ended (data={"reason": str})
]

Scheduling = Literal["interrupt", "when_idle", "silent"]


@dataclass
class VoiceEvent:
    kind: EventKind
    at: float = field(default_factory=time.monotonic)
    text: str = ""
    audio: bytes = b""
    rate: int = 0
    call_id: str = ""
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    data: dict[str, Any] = field(default_factory=dict)


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class ToolSpec:
    """One tool the voice model may call.

    `parameters` is a JSON schema object. `non_blocking` lets the model keep talking while
    the handler runs (providers without that notion ignore it). `scheduling` says how the
    result is delivered: "interrupt" makes the model speak about it now, "when_idle" after
    it finishes its current sentence, "silent" only adds it to context.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    non_blocking: bool = True
    scheduling: Scheduling = "interrupt"
    # Optional result returned the instant the call arrives (e.g. {"status": "looking"}),
    # before the handler finishes; the final result follows. Providers that support partial
    # function results (Gemini: will_continue) use it to make the model acknowledge at once.
    interim: dict[str, Any] | None = None
    interim_scheduling: Scheduling = "interrupt"


@dataclass
class VoiceConfig:
    model: str
    system_prompt: str = ""
    voice: str | None = None
    tools: list[ToolSpec] = field(default_factory=list)
    input_rate: int = 16000
    transcribe_input: bool = True
    transcribe_output: bool = True
    # End-of-speech detection: how long the user must be silent before the model replies.
    silence_ms: int | None = None
    # True: the provider's voice activity detection is off and the caller marks turns with
    # begin_user_turn()/end_user_turn() (push-to-talk or a local VAD).
    client_turns: bool = False
    # Provider-specific knobs (e.g. {"thinking_level": "low"}) that have no neutral meaning.
    extra: dict[str, Any] = field(default_factory=dict)
    # What the session knows before it starts, as (role, text) messages: "developer", "user" or
    # "assistant" (GPT-Live: `session.input`, at most 128 messages and 8,192 tokens).
    history: list[tuple[str, str]] = field(default_factory=list)


class VoiceSession(abc.ABC):
    """Lifecycle: `await start()`, then push input and iterate `events()` concurrently,
    then `await close()`. Also usable as `async with`."""

    config: VoiceConfig
    closed: bool = False  # set once the session has ended (by either side); nothing more is sent

    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def close(self) -> None: ...

    @abc.abstractmethod
    async def send_audio(self, pcm: bytes) -> None:
        """Microphone audio: 16-bit mono PCM at config.input_rate. Send continuously
        (including silence) so the provider's voice activity detection can end turns."""

    async def send_frame(self, jpeg: bytes) -> None:
        """A video frame (JPEG). Providers without video input ignore it."""

    async def begin_user_turn(self) -> None:
        """With client-side turn detection (VoiceConfig.client_turns): the user started
        speaking (push-to-talk pressed, or a local VAD fired)."""
        raise NotImplementedError

    async def end_user_turn(self) -> None:
        """With client-side turn detection: the user finished; the model answers now
        instead of waiting for the provider's silence timeout."""
        raise NotImplementedError

    @abc.abstractmethod
    async def send_text(self, text: str) -> None:
        """A typed user message; the model answers it like speech."""

    @abc.abstractmethod
    async def inject(self, text: str, *, respond: bool = False, delegation_id: str | None = None,
                     source: str = "app", facts: dict | None = None) -> None:
        """Add context the model did not ask for (e.g. "the user clicked the oval; it is
        outlined"). respond=True asks the model to speak about it now. `source` and `facts` are
        for the log only (the voice-note record of silent context), never sent."""

    async def mute(self, muted: bool) -> None:
        """The user muted or unmuted the microphone (providers that can be told, are)."""
        return None

    def idle_since(self) -> float:
        """Seconds since the conversation last had speech in it (0 while someone speaks).
        The narrator waits for a pause before saying anything unasked."""
        return float("inf")

    def last_speech_at(self) -> float:
        """time.monotonic() of the model's last audible speech (0 if none yet)."""
        return 0.0

    @abc.abstractmethod
    def events(self) -> AsyncIterator[VoiceEvent]:
        """Every event of the session, in arrival order, until it closes."""

    def on(self, callback: Callable[[VoiceEvent], None]) -> None:
        """Also deliver every event to `callback` (synchronously, as it arrives)."""
        self._callbacks.append(callback)

    @property
    def _callbacks(self) -> list[Callable[[VoiceEvent], None]]:
        if not hasattr(self, "_cb_list"):
            self._cb_list: list[Callable[[VoiceEvent], None]] = []
        return self._cb_list

    async def __aenter__(self) -> "VoiceSession":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()
