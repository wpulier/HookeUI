"""Gemini 3.8 Live as the one-brain voice (option VOICE `gemini-3.8-live`), beside `gpt-realtime-2.1`: it hears, sees
and acts in one context, bound to the app by the same `Partner` (voice/partner.py) through `PartnerSession`
(voice/partner_session.py). Research: hooke-web docs/research/2026-10-29-live-partner/gemini-live.md.

Transport: Gemini Live is a WebSocket only (no WebRTC, no sideband), so this server holds the one connection
(google-genai's `client.aio.live.connect`) and the audio is relayed: the studio's microphone PCM comes over /ws/voice
(24 kHz, which Gemini resamples) and the voice's 24 kHz PCM goes back as `MMA1` frames (voice/route.py's relay path).
The studio still plays it and reports `voice-phase`, which says when it is audible.

What the session does, as `RealtimeSession` does for the partner (the same `VoiceEvent`s and `partner-*` records):
  - hold to talk (TALK "hold"): automatic activity detection is off; a key held past MIN_HOLD_S opens an activity
    (`activity_start`, then the audio held since the key went down) and its release closes it (`activity_end`, after
    RELEASE_TAIL_S), and Gemini answers then. A tap or Escape opens nothing. A key down while it speaks stops it
    (`activity_start` at once interrupts the server's generation; what is still coming of it is dropped here). Open mic
    (TALK "open") uses Gemini's own voice activity detection;
  - perception: each turn's text note and pictures (camera, window) go in as client content (user role, labelled as
    the app's, never completing the turn) while the key is held, and the camera streams at VIDEO_FPS as video while
    Talk is open (`send_realtime_input(video=...)`, the newest frame, off the loop). Gemini has no item delete: earlier
    pictures stay in the context until its compression slides them out; the notes are kept here for a fresh connection;
  - tools are declared NON_BLOCKING (3.8 Live's default). A tool call runs its ToolSpec handler; a duplicate of a call
    still running (Gemini's documented repeat) is answered with the first one's result, never run twice. The results of
    one tool_call message go back together, never empty (status, retryable, message), with a fresh look first when a
    tool changed the screen (act, then look). Scheduling: INTERRUPT once the voice's current words have played (its
    WHEN_IDLE ended the turn without saying the result, on the API 2026-10-29), SILENT when a newer turn has begun or
    the call only pointed after the answer was spoken;
  - lines nobody asked for, typed lines and progress lines are client content that completes a turn (Gemini answers
    it), sent only when nobody talks and nothing is said, once per event as for the OpenAI voice;
  - session length: context-window compression (COMPRESS_AT -> COMPRESS_TO tokens) and resumption handles; a GoAway
    renews the connection with the newest handle when it is quiet (before its time runs out), as does a change of tools
    (Gemini takes no mid-session tool update on the Gemini API); a dropped connection is resumed the same way (the
    partner asks), and without a handle a fresh connection is seeded with the conversation, the notes and the brief.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import time
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable

from .base import ToolSpec, VoiceConfig, VoiceEvent, VoiceSession
from .openai_realtime import Perception, Waiting, usage_usd

MODEL = "gemini-3.8-live"  # option VOICE's Gemini one-brain choice (catalog/models/gemini-3-8-live)
RATE_OUT = 24000
MIN_HOLD_S = 0.25  # a press shorter than this is a tap: no activity is opened, so nothing is answered
RELEASE_TAIL_S = 0.05  # the relayed audio still in flight when the key is released (the studio sends it in order)
EARLY_WAIT_S = 0.6  # perception waits this long at most for its turn's activity to open
HELD_AUDIO_S = 30.0  # audio held while the key is down and the activity not yet open, at most
# Audio just before the key-down is read is kept and goes in with the turn: the studio sends the key-down and then its
# microphone on one socket, but the key-down is handled in a task of its own, so its first chunks may come first.
PREROLL_S = 0.3
IDLE_S = 1.5  # nobody has spoken for this long: a line nobody asked for may be said
PROACTIVE_KEPT_S = 60.0
SAME_EVENT_S = 120.0
RECONNECT_WAIT_S = 10.0
RESULT_QUIET_WAIT_S = 3.0  # a tool's result waits at most this long for the voice's current words to end
ANSWER_WAIT_S = 4.0  # something Gemini was asked to answer counts as being said this long, or until its answer begins
PROGRESS_AFTER_S = 1.2  # a tool round this long without its results, and the voice quiet: one progress line
PROGRESS_LINES = 1
NOTES_KEPT = 12
CLOSE_STEP_S = 2.0
GOAWAY_MARGIN_S = 10.0  # a GoAway's renewal waits for quiet, but never past its time left minus this
VIDEO_FPS = 1.0  # Gemini Live's video input takes at most one frame a second
VIDEO_SIDE = 768
COMPRESS_AT, COMPRESS_TO = 64_000, 32_000  # tokens: half the 128K window, so each turn's re-read context stays cheap
# Words its transcription should hear right (`AudioTranscriptionConfig.custom_vocabulary`, new in 3.8 Live): the
# microscope's, the app's and the names its tools answer to.
VOCABULARY = ("Hooke", "HookeUI", "microscope", "eyepiece", "objective", "slide", "specimen", "stain", "nuclei",
              "nucleus", "cytoplasm", "mitosis", "metaphase", "anaphase", "chromosome", "stoma", "stomata",
              "guard cell", "epidermis", "Paramecium", "Euglena", "diatom", "Volvox", "amoeba", "algae", "yeast",
              "bacteria", "erythrocyte", "leukocyte", "lymphocyte", "neutrophil", "platelet", "hemocytometer",
              "Cellpose", "micro-sam", "BioCLIP", "Fiji", "ImageJ", "outline", "researcher", "Project", "Tab")
AUDIO_TOKENS_PER_S = 25  # Gemini's audio tokenization (32 tokens a second in 2.x docs; 25 measured on 3.8 Live)


def item_text(text: str) -> str:
    """The app's own context as Gemini takes it (user role, its client content has no system role): marked as the
    app's, so it is never taken for the user's words."""
    return "(From Hooke, not the user speaking.) " + text


def payload(result: Any) -> dict:
    """A tool's result as the model is given it: never empty, always a status, retryable and message (Gemini 3.8 Live
    repeats a call 2-5 times when a result says nothing; Google's migration guide)."""
    out = {k: v for k, v in (result if isinstance(result, dict) else {"result": result}).items()
           if not str(k).startswith("_")}
    if "status" not in out:
        out["status"] = ("still_working" if out.get("still_working") or out.get("ok") is None and "ok" in out
                         else "failed" if out.get("ok") is False or out.get("error") else "done")
    out.setdefault("retryable", False)
    if not out.get("message"):
        out["message"] = str(out.get("summary") or out.get("error") or out.get("note") or out.get("answer") or {
            "done": "done; nothing more to report", "failed": "it did not work",
            "still_working": "still running; its outcome is told to you when it lands"}.get(out["status"], "done"))[:600]
    if out["status"] == "failed":
        out["message"] += " Do not call it again for this request: say plainly what failed and what to try."
    return out


def usage_facts(usage) -> dict:
    """A turn's `usage_metadata` as the meter and the bench read OpenAI's: tokens by kind (in by modality, out by
    modality; thoughts counted as text out)."""
    facts = {"inText": 0, "inAudio": 0, "inImage": 0, "inCached": 0, "outText": 0, "outAudio": 0}
    if usage is None:
        return facts
    for row in getattr(usage, "prompt_tokens_details", None) or []:
        kind = str(getattr(getattr(row, "modality", None), "name", getattr(row, "modality", "")) or "").upper()
        key = "inAudio" if kind == "AUDIO" else "inImage" if kind in ("IMAGE", "VIDEO") else "inText"
        facts[key] += int(getattr(row, "token_count", 0) or 0)
    if not any(facts[k] for k in ("inText", "inAudio", "inImage")):
        facts["inText"] = int(getattr(usage, "prompt_token_count", 0) or 0)
    facts["inCached"] = int(getattr(usage, "cached_content_token_count", 0) or 0)
    out = 0
    for row in getattr(usage, "response_tokens_details", None) or []:
        kind = str(getattr(getattr(row, "modality", None), "name", getattr(row, "modality", "")) or "").upper()
        key = "outAudio" if kind == "AUDIO" else "outText"
        facts[key] += int(getattr(row, "token_count", 0) or 0)
        out += int(getattr(row, "token_count", 0) or 0)
    facts["outText"] += max(0, int(getattr(usage, "response_token_count", 0) or 0) - out)
    facts["outText"] += int(getattr(usage, "thoughts_token_count", 0) or 0)
    return facts


def build_config(config: VoiceConfig, *, talk: str = "hold", handle: str | None = None, fresh: bool = True):
    """The session's LiveConnectConfig. `fresh`: a new conversation, whose history this server sends as client content
    (HistoryConfig); a resumed one keeps its own. Recognised `config.extra`: thinking_budget (default 0: first audio
    0.96-1.22 s against 1.27-1.47 s with thinking on, docs/bench/live.md of 2026-10-03; 3.8 Live refuses
    thinking_level), media_resolution (default "low": 70 tokens a video frame), compress_at / compress_to, vocabulary
    (a list, or [] for none), silence_ms (open mic only)."""
    from google.genai import types

    extra = config.extra
    declarations = [types.FunctionDeclaration(name=t.name, description=t.description,
                                              parameters_json_schema=t.parameters or {"type": "object"},
                                              behavior=types.Behavior.NON_BLOCKING) for t in config.tools]
    vocabulary = list(extra.get("vocabulary", VOCABULARY))
    live = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=config.system_prompt or None,
        tools=[types.Tool(function_declarations=declarations)] if declarations else None,
        input_audio_transcription=types.AudioTranscriptionConfig(custom_vocabulary=vocabulary or None)
        if config.transcribe_input else None,
        output_audio_transcription=types.AudioTranscriptionConfig() if config.transcribe_output else None,
        session_resumption=types.SessionResumptionConfig(handle=handle),
        context_window_compression=types.ContextWindowCompressionConfig(
            trigger_tokens=int(extra.get("compress_at", COMPRESS_AT)),
            sliding_window=types.SlidingWindow(target_tokens=int(extra.get("compress_to", COMPRESS_TO)))),
        media_resolution=getattr(types.MediaResolution,
                                 f"MEDIA_RESOLUTION_{str(extra.get('media_resolution', 'low')).upper()}"),
    )
    if fresh:
        live.history_config = types.HistoryConfig(initial_history_in_client_content=True)
    if config.voice:
        live.speech_config = types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=config.voice)))
    if extra.get("thinking_budget", 0) is not None:
        live.thinking_config = types.ThinkingConfig(thinking_budget=int(extra.get("thinking_budget", 0)))
    if talk == "hold":
        live.realtime_input_config = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True))
    elif config.silence_ms is not None:
        live.realtime_input_config = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(silence_duration_ms=int(config.silence_ms)))
    return live


class GeminiPartnerSession(VoiceSession):
    link = "relay"

    def __init__(self, config: VoiceConfig, *, api_key: str | None = None, talk: str = "hold",
                 connect: Callable[..., Any] | None = None, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        """`connect(model=..., config=...)` returns an async context manager yielding a live session (send_realtime_input,
        send_client_content, send_tool_response, receive); google-genai's `client.aio.live.connect` by default. Tests
        pass a fake."""
        self.config = config
        self.talk = talk
        self.browser_client = None
        self._api_key, self._connect = api_key, connect
        self.clock, self.wall = clock, wall
        self.noted: Callable[[dict], None] | None = None
        self.answer_sdp: str | None = None  # no WebRTC: nothing for the browser to apply
        self.session_id: str | None = None
        self.closed = False
        self.termination = ""
        self.usage_seconds = None  # billed by tokens (usage events)
        self.last_commentary_at = 0.0
        self.spoken_actions: set[str] = set()
        self.opened_at = clock()
        # The partner's hooks (voice/partner_session.py).
        self.history: Callable[[], list[tuple[str, str]]] = lambda: list(config.history)
        self.turn_items: Callable[[int], Perception] = lambda n: Perception()
        self.on_speech_started: Callable[[int], None] = lambda n: None
        self.tag_notice: Callable[[str], dict | None] = lambda text: None
        self.on_point: Callable[[list[str]], None] = lambda targets: None
        self.running_facts: Callable[[], str] = lambda: ""
        # The camera for the video stream: () -> (jpeg, frame id, crop) or None, off the loop (the route sets it).
        self.video: Callable[[], Awaitable[Any]] | None = None
        # --- the connection ---
        self._cm = None
        self._live = None
        self._recv_task: asyncio.Task | None = None
        self._tasks: set = set()
        self._queue: asyncio.Queue[VoiceEvent] = asyncio.Queue()
        self._connected = asyncio.Event()
        self._reconnecting = False
        self._epoch = 0
        self.resume_handle: str | None = None  # the newest resumption handle
        self.resumed = False
        self.reconnects = 0
        self._said_reconnect_at = -1e9
        self._goaway_at: float | None = None  # when the server's GoAway said the connection ends (monotonic)
        self._tools_pending = False
        # --- turns ---
        self.n = 0
        self._held: int | None = None
        self._held_at = 0.0
        self._held_epoch = 0
        self._activity: int | None = None  # the turn whose activity is open
        self._opened: dict[int, asyncio.Event] = {}
        self._starting: asyncio.Task | None = None
        self._held_audio: list[bytes] = []
        self._preroll: list[bytes] = []
        self._cancelled: set[int] = set()
        self._muted = False
        self._last_user_at = 0.0
        # --- what is being said ---
        self._gen: dict | None = None  # the generation in progress: id, cause, n, firstAudioT, transcript, ...
        self._done: dict | None = None  # the last generation ended, waiting for its turn's usage
        # What the next generation answers (its cause and turn): "tap" when it answers a tap or Escape after a barge-in,
        # which nothing of is heard or filed.
        self._next_cause: tuple[str, int] = ("unknown", 0)
        self._awaiting: float | None = None  # when Gemini was last asked for an answer that has not begun (monotonic)
        self._flushing = False  # the audio held for an activity is being sent: newer audio queues behind it
        self._gens = 0
        self._audio_until = 0.0  # when the audio sent to the studio ends playing (monotonic estimate)
        self._last_audio_at = 0.0
        self._studio_speaking = False
        self._heard: list[str] = []  # the user's words of the turn being heard, until the model answers
        self._heard_n = 0
        self._point: list[str] = []
        self._spoke_in_turn: set[int] = set()
        # --- tools ---
        self._calls: dict[str, asyncio.Task] = {}
        self._call_keys: dict[tuple, asyncio.Task] = {}
        self._seen_calls: set[str] = set()
        self._withdrawn: set[str] = set()
        self._rounds: int = 0  # tool rounds running
        # --- context ---
        self.brief_text = ""
        self._brief_revision: str | None = None
        self.notes: list[str] = []
        self._waiting: list[Waiting] = []
        self._said_events: dict[tuple, float] = {}
        self._said_texts: dict[tuple, str] = {}
        self._watch_task: asyncio.Task | None = None
        self._video_task: asyncio.Task | None = None
        self._last_frame_id = None
        self.frames_sent = 0

    # --- lifecycle ---------------------------------------------------------------------------------------
    def _default_connect(self):
        from google import genai

        client = genai.Client(api_key=self._api_key or os.environ["GEMINI_API_KEY"])
        return client.aio.live.connect

    async def start(self, offer_sdp: str | None = None) -> None:
        if self._connect is None:
            self._connect = self._default_connect()
        await self._open(resume=False)
        self._watch_task = asyncio.create_task(self._watch())
        if self.video is not None and float(self.config.extra.get("video_fps", VIDEO_FPS)) > 0:
            self._video_task = asyncio.create_task(self._stream_video())
        self._emit(VoiceEvent("ready", data={"session_id": self.session_id, "link": "relay"}))

    async def _open(self, *, resume: bool) -> None:
        """A connection: resumed with the newest handle when asked and there is one (its conversation goes on), else
        fresh and seeded. A handle the server refuses falls back to a fresh one."""
        handle = self.resume_handle if resume else None
        try:
            cm = self._connect(model=self.config.model, config=build_config(self.config, talk=self.talk, handle=handle,
                                                                            fresh=handle is None))
            live = await cm.__aenter__()
        except Exception:
            if handle is None:
                raise
            self._log({"kind": "partner-resume-refused", "why": "the server refused the handle; a fresh connection"})
            handle = None
            cm = self._connect(model=self.config.model, config=build_config(self.config, talk=self.talk, fresh=True))
            live = await cm.__aenter__()
        self._cm, self._live = cm, live
        self._epoch += 1
        self.resumed = handle is not None
        self.closed = False
        self.opened_at = self.clock()
        self._goaway_at = None
        self.session_id = self.session_id or f"gl_{uuid.uuid4().hex[:16]}"
        self._activity, self._gen, self._done = None, None, None
        self._recv_task = asyncio.create_task(self._receive(live, self._epoch))
        self._connected.set()
        if not self.resumed:
            await self._seed()

    async def _seed(self) -> None:
        """What a fresh connection starts knowing: the recent conversation, the notes of what was seen and the brief, as
        client content (HistoryConfig); the last part completes the turn, which Gemini does not answer."""
        from google.genai import types

        turns = []
        for role, text in self.history()[-16:]:
            if text.strip():
                turns.append(types.Content(role="model" if role == "assistant" else "user",
                                           parts=[types.Part(text=text if role in ("user", "assistant")
                                                             else item_text(text))]))
        if self.notes:
            turns.append(types.Content(role="user", parts=[types.Part(text=item_text(
                "What was seen on earlier turns (context only):\n" + "\n".join(self.notes[-NOTES_KEPT:])))]))
        if self.brief_text:
            turns.append(types.Content(role="user", parts=[types.Part(text=item_text(
                "The Project now (context only, kept current by the app):\n" + self.brief_text))]))
        if turns:
            await self._live.send_client_content(turns=turns, turn_complete=True)

    async def close(self) -> None:
        if self.closed and self._cm is None:
            return
        self.closed = True
        for task in (self._watch_task, self._video_task, self._starting):
            if task is not None:
                task.cancel()
        unresolved = await self._end(self._cm, self._recv_task)
        self._cm = self._live = self._recv_task = None
        self._connected.clear()
        self.termination = "confirmed" if not unresolved else "unconfirmed: " + "; ".join(unresolved)
        self._emit(VoiceEvent("closed", data={"reason": "client"}))

    async def _end(self, cm, task) -> list[str]:
        unresolved = []
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(task, CLOSE_STEP_S)
        if cm is not None:
            try:
                await asyncio.wait_for(cm.__aexit__(None, None, None), CLOSE_STEP_S)
            except Exception:
                unresolved.append("its connection didn't close")
        return unresolved

    async def reconnect(self, offer_sdp: str | None = None, *, why: str = "close") -> float:
        """The connection renewed on the same session object: resumed with the newest handle (nothing seeded again),
        else fresh and seeded. The old connection is closed in the background. Returns the seconds it took."""
        started = self.clock()
        self._reconnecting = True
        self._connected.clear()
        mid_speech = self.speaking()
        old = (self._cm, self._recv_task)
        self._cm = self._live = self._recv_task = None
        try:
            await self._open(resume=True)
        finally:
            self._reconnecting = False
        self._spawn(self._end(*old))
        self._tools_pending = False
        self.reconnects += 1
        took = self.clock() - started
        self._log({"kind": "partner-reconnect", "reason": why, "closedT": self.wall() - took, "readyT": self.wall(),
                   "ms": round(took * 1000), "midSpeech": mid_speech, "newCall": not self.resumed,
                   "resumed": self.resumed})
        if mid_speech and why not in ("tools", "goaway") and self.clock() - self._said_reconnect_at > 60:
            self._said_reconnect_at = self.clock()
            await self.proactive("The connection was renewed while you were speaking; say only: Reconnected, go on.",
                                 event="reconnect", ref=str(self.reconnects))
        return took

    def connected(self) -> bool:
        return self._live is not None and self._connected.is_set() and not self.closed

    async def drop_connection(self) -> None:
        """The bench's fault: the connection closed under the session."""
        cm = self._cm
        if cm is not None:
            with contextlib.suppress(Exception):
                await cm.__aexit__(None, None, None)

    async def _wait_connected(self, seconds: float) -> bool:
        if self.connected():
            return True
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._connected.wait(), seconds)
        return self.connected()

    # --- sending ---------------------------------------------------------------------------------------------
    async def _content(self, parts: list, *, complete: bool = False) -> None:
        from google.genai import types

        if self._live is None:
            raise ConnectionError("the Gemini Live connection is not open")
        await self._live.send_client_content(turns=types.Content(role="user", parts=parts), turn_complete=complete)

    async def _text(self, text: str, *, complete: bool = False) -> None:
        from google.genai import types

        await self._content([types.Part(text=text)], complete=complete)

    async def _image(self, jpeg: bytes, label: str) -> None:
        from google.genai import types

        await self._content([types.Part(text=item_text(label)), types.Part(inline_data=types.Blob(
            data=jpeg, mime_type="image/jpeg"))])

    async def _realtime(self, **kw) -> None:
        if self._live is None:
            raise ConnectionError("the Gemini Live connection is not open")
        await self._live.send_realtime_input(**kw)

    # --- the Project brief and the tools ---------------------------------------------------------------------
    def seed_brief(self, text: str, revision: str) -> None:
        self.brief_text, self._brief_revision = text, revision

    async def set_brief(self, text: str, revision: str) -> None:
        """The Project brief, added again only when its revision changed (Gemini has no item delete: the newest says it
        replaces the ones before)."""
        if self._brief_revision == revision and self.brief_text:
            return
        self.brief_text, self._brief_revision = text, revision
        await self._text(item_text("The Project now (context only, kept current by the app; it replaces any earlier "
                                   "Project text):\n" + text))

    async def update_tools(self, tools) -> None:
        """The tools changed (a capability turned on): Gemini takes them with a new connection, so the session resumes
        with them when it is next quiet."""
        self.config.tools = list(tools)
        self._tools_pending = True
        self._log({"kind": "partner-tools", "count": len(self.config.tools), "applied": "when quiet"})

    # --- talking ---------------------------------------------------------------------------------------------
    def speaking(self) -> bool:
        """Generating, about to (asked for an answer that has not begun), or audible. A generation waiting on its tool
        calls counts: Gemini ends it only after their results."""
        if self._awaiting is not None and self.clock() - self._awaiting > ANSWER_WAIT_S:
            self._awaiting = None  # nothing came of it (Gemini may stay silent)
        return ((self._gen is not None and not self._gen.get("suppressed")) or self._awaiting is not None
                or self.audible())

    def audible(self) -> bool:
        """Its words are playing in the studio: as the studio says, or as the audio sent there lasts."""
        return self._studio_speaking or self.clock() < self._audio_until

    def studio_phase(self, phase: str) -> None:
        """The studio's own measured state of its audio (`voice-phase`): the truth of when the voice is audible."""
        now = self.clock()
        if phase == "speaking":
            self._last_audio_at = now
            gen = self._gen or self._done
            if gen is not None and gen.get("heardT") is None:
                gen["heardT"] = self.wall()
            if not self._studio_speaking:
                self._studio_speaking = True
                self._emit(VoiceEvent("vad", data={"state": "model-start"}))
        elif phase == "listening" and self._studio_speaking:
            self._studio_speaking = False
            self._last_audio_at = now
            self._audio_until = min(self._audio_until, now)
            rid = (self._done or self._gen or {}).get("id")
            self._log({"kind": "partner-audio", "responseId": rid, "audioEndT": self.wall(), "how": "stopped"})
            self._emit(VoiceEvent("turn_complete", data={"reason": "quiet"}))

    async def talk_down(self, n: int | None = None) -> None:
        """The talk key went down: the voice stops if it is talking (barge-in), and the turn starts (its activity opens
        once the key has been held past MIN_HOLD_S, with the audio held since)."""
        self.n = n if isinstance(n, int) else self.n + 1
        barge = self.speaking()
        self._held, self._held_at, self._held_epoch = self.n, self.clock(), self._epoch
        self._held_audio, self._preroll = self._preroll, []
        self._last_user_at = self.clock()
        self._opened.setdefault(self.n, asyncio.Event())
        self._log({"kind": "partner-talk", "n": self.n, "down": True, "barge": barge})
        generating = self._gen is not None and not self._gen.get("suppressed")
        if barge:
            await self.stop_speaking()
        if generating:
            await self._open_activity(self.n)  # the server stops generating only when an activity starts
        else:
            if self._starting is not None:
                self._starting.cancel()
            self._starting = asyncio.ensure_future(self._start_after(self.n))

    async def stop_speaking(self) -> None:
        cancel_at = self.wall()
        if self._gen is not None:
            self._gen["suppressed"] = True  # what is still coming of it is not heard nor filed
            self._gen["status"] = "cancelled"
        self._audio_until = self.clock()
        self._studio_speaking = False
        self._emit(VoiceEvent("interrupted"))
        self._log({"kind": "partner-barge", "n": self.n, "cancelT": cancel_at, "clearT": self.wall()})

    async def _start_after(self, n: int) -> None:
        await asyncio.sleep(MIN_HOLD_S)
        if self._held == n:
            await self._open_activity(n)

    async def _open_activity(self, n: int) -> None:
        """Turn n's activity: `activity_start`, then the audio held since its key went down."""
        if self._activity == n or self._live is None:
            return
        self._activity, self._flushing = n, True
        try:
            from google.genai import types

            await self._realtime(activity_start=types.ActivityStart())
            while self._held_audio:  # in order: audio arriving meanwhile queues behind (send_audio)
                await self._send_pcm(self._held_audio.pop(0))
        except Exception as exc:  # reconnecting: the release finds the connection replaced and says so
            self._log({"kind": "partner-talk-failed", "n": n, "error": f"{type(exc).__name__}: {exc}"[:300]})
        finally:
            self._flushing = False
        self._opened.setdefault(n, asyncio.Event()).set()

    async def _close_activity(self, *, answered: bool) -> None:
        if self._activity is None:
            return
        n, self._activity = self._activity, None
        if not answered:
            self._next_cause = ("tap", n)  # a tap or Escape after a barge-in: whatever it answers is never heard
        with contextlib.suppress(Exception):
            from google.genai import types

            await self._realtime(activity_end=types.ActivityEnd())

    async def talk_up(self, n: int | None = None) -> bool:
        """The talk key was released: what of the turn's perception is not in yet goes in, the activity is closed after
        the audio's tail and Gemini answers. False when nothing is asked: a tap, or a turn whose audio went to a
        connection since replaced."""
        if self._held is None or (isinstance(n, int) and n != self._held):
            return False
        n, held_for, held_epoch = self._held, self.clock() - self._held_at, self._held_epoch
        self._held = None
        released = self.wall()
        self._log({"kind": "partner-talk", "n": n, "down": False})
        if held_for < MIN_HOLD_S:
            await self._forget_turn(n)
            return False
        if self._starting is not None and not self._starting.done():
            with contextlib.suppress(BaseException):
                await asyncio.shield(self._starting)  # an activity opening now is let finish (its held audio in it)
        seen = self.turn_items(n)  # as it was at key-down, taken now
        if not await self._wait_connected(RECONNECT_WAIT_S):
            self._log({"kind": "partner-turn-lost", "n": n, "why": "no connection came back"})
            return False
        if self._epoch != held_epoch:
            self._log({"kind": "partner-turn-lost", "n": n, "why": "the connection was renewed while the key was held"})
            self._activity = None
            await self._ask("reconnect", n, "The connection dropped for a moment while the user was speaking. Say only, "
                                            "in your own words: please say that again.")
            return False
        await self._open_activity(n)
        await self._perceived(n, seen)
        await asyncio.sleep(RELEASE_TAIL_S)
        self._next_cause, self._heard_n = ("turn", n), n
        await self._close_activity(answered=True)
        self._awaiting = self.clock()
        committed = self.wall()
        self._last_user_at = self.clock()
        self._log({"kind": "partner-turn", "n": n, "how": "hold", "releasedT": released, "committedT": committed,
                   "responseCreatedT": committed, **seen.facts})
        return True

    async def talk_cancel(self, n: int | None = None) -> None:
        if self._held is None:
            return
        n, self._held = self._held, None
        self._log({"kind": "partner-talk", "n": n, "down": False, "cancel": True})
        await self._forget_turn(n)

    async def _forget_turn(self, n: int) -> None:
        """A tap or Escape: nothing of it is asked (an activity a barge-in opened is closed, and its answer dropped)."""
        self._cancelled.add(n)
        if self._starting is not None:
            self._starting.cancel()
        self._held_audio = []
        if self._activity == n:
            await self._close_activity(answered=False)
        self._opened.setdefault(n, asyncio.Event()).set()  # perception waiting on it gives up

    async def early(self, n: int, *, note: str = "", image: tuple[bytes, str] | None = None, detail: str = "auto") -> bool:
        """Part of turn n's perception sent while its key is held, once its activity is open (a tap sends none of it).
        False when the turn is over or the connection is away: the release sends it."""
        if self.talk == "hold":
            opened = self._opened.setdefault(n, asyncio.Event())
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(opened.wait(), EARLY_WAIT_S)
        if n != self.n or n in self._cancelled or not self.connected() or (self.talk == "hold" and self._activity != n):
            return False
        try:
            if note:
                self._keep_note(note)
                await self._text(item_text(note))
            if image is not None:
                await self._image(image[0], image[1])
        except Exception:
            return False
        return True

    def _keep_note(self, note: str) -> None:
        self.notes.append(note[:600])
        del self.notes[:-NOTES_KEPT]

    async def _perceived(self, n: int, seen: Perception) -> None:
        if seen.note:
            self._keep_note(seen.note)
            await self._text(item_text(seen.note))
        for jpeg, label in seen.images:
            await self._image(jpeg, label)

    # --- input -----------------------------------------------------------------------------------------------
    async def _send_pcm(self, pcm: bytes) -> None:
        from google.genai import types

        await self._realtime(audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={self.config.input_rate}"))

    async def send_audio(self, pcm: bytes) -> None:
        """The studio's microphone, relayed: in hold mode only while the key is held (held until its activity opens) and
        the release's tail; with an open mic all of it, unless muted."""
        if self.closed or self._live is None or not pcm:
            return
        if self.talk == "open":
            if not self._muted:
                with contextlib.suppress(Exception):
                    await self._send_pcm(pcm)
            return
        if self._activity is not None and not self._flushing:
            with contextlib.suppress(Exception):
                await self._send_pcm(pcm)
        elif self._held is not None or self._flushing:
            self._held_audio.append(pcm)
            self._trim(self._held_audio, HELD_AUDIO_S)
        else:
            self._preroll.append(pcm)
            self._trim(self._preroll, PREROLL_S)

    def _trim(self, chunks: list[bytes], seconds: float) -> None:
        limit = int(seconds * self.config.input_rate * 2)
        while len(chunks) > 1 and sum(len(c) for c in chunks) > limit:
            chunks.pop(0)

    async def mute(self, muted: bool) -> None:
        self._muted = bool(muted)

    async def send_text(self, text: str) -> None:
        """A typed line while talking: a user turn like speech."""
        if self.closed or not text.strip() or not self.connected():
            return
        self._last_user_at = self.clock()
        self._next_cause = ("typed", self.n)
        await self._text(text, complete=True)
        self._awaiting = self.clock()

    async def inject(self, text: str, *, respond: bool = False, delegation_id: str | None = None,
                     source: str = "app", facts: dict | None = None) -> None:
        """Context from the app (the narrator's lines, researchers' news, typed chat). respond=True: said as one line
        when nobody is talking (`proactive`); otherwise context, never answered."""
        if self.closed:
            return
        if respond:
            tag = self.tag_notice(text) or {}
            event = (facts or {}).get("event") or tag.get("event") or (source if source != "app" else "notice")
            await self.proactive(text, event=event, ref=(facts or {}).get("ref") or tag.get("ref"),
                                 before=tag.get("before"), mark=tag.get("mark"))
            return
        if not self.connected():
            self._log({"kind": "partner-note-unsent", "source": source, "why": "reconnecting", "chars": len(text)})
            return
        await self._text(item_text("Context only, not a request; do not answer it:\n" + text))
        if self.noted is not None:
            with contextlib.suppress(Exception):
                self.noted({"kind": "voice-note", "source": source, "sessionId": self.session_id, **(facts or {}),
                            "chars": len(text), "text": text})

    def idle_since(self) -> float:
        if self._held is not None or self.speaking():
            return 0.0
        last = max(self._last_audio_at, self._last_user_at)
        return self.clock() - last if last else float("inf")

    def last_speech_at(self) -> float:
        return self._last_audio_at

    def user_busy(self) -> bool:
        return self._held is not None or (self.clock() - self._last_user_at < IDLE_S if self._last_user_at else False)

    def user_talking(self) -> bool:
        return self._held is not None

    # --- asking it to speak (client content that completes a turn) -------------------------------------------
    async def _ask(self, cause: str, n: int, instruction: str, facts: str = "") -> bool:
        """A turn of the app's own that Gemini answers: facts, then what to say. Never while the key is held."""
        if self._held is not None or not self.connected():
            return False
        self._next_cause = (cause, n)
        text = (item_text(facts) + "\n" if facts else "") + item_text(instruction)
        try:
            await self._text(text, complete=True)
        except Exception:
            return False
        self._awaiting = self.clock()
        return True

    async def proactive(self, text: str, *, event: str, ref: str | None = None,
                        before: Callable[[], Awaitable[None]] | None = None, mark: str | None = None) -> None:
        """One sentence about something that happened, said only when nobody is talking, once per event (its kind and
        reference, or its whole text's digest)."""
        key = (event, ref) if ref else (event, hashlib.sha256(text.encode()).hexdigest())
        now = self.clock()
        if now - self._said_events.get(key, -1e9) < SAME_EVENT_S:
            self._log({"kind": "partner-proactive", "event": event, "ref": ref, "eventT": self.wall(),
                       "userBusy": self.user_busy(), "skipped": "duplicate"})
            if self._said_texts.get(key) != text and self.connected():
                self._said_texts[key] = text
                with contextlib.suppress(Exception):
                    await self._text(item_text("More about what was just said (context only):\n" + text))
            return
        self._said_texts[key] = text
        self._said_events[key] = now
        self._waiting.append(Waiting(text, event, ref, now, before, mark, self.wall()))
        await self._deliver()

    async def _deliver(self) -> None:
        while self._waiting and not self.closed:
            line = self._waiting[0]
            if self.clock() - line.at > PROACTIVE_KEPT_S:
                self._waiting.pop(0)
                with contextlib.suppress(Exception):
                    await self._text(item_text("Happened while the user was talking (context only): " + line.text))
                self._log({"kind": "partner-proactive", "event": line.event, "ref": line.ref, "eventT": line.wall,
                           "userBusy": True, "skipped": "user-talking"})
                continue
            if self.user_busy() or self.speaking() or self._calls or not self.connected():
                return  # the watch tries again when it is quiet
            self._waiting.pop(0)
            if line.before is not None:
                with contextlib.suppress(Exception):
                    await line.before()
            if self.user_busy() or self._studio_speaking:  # the user pressed talk while it was prepared
                self._waiting.insert(0, line)
                return
            asked = await self._ask("proactive", self.n, "Now say exactly one short spoken sentence telling the user "
                                    "what just happened, from the facts above. No question, no offer, no tool.",
                                    "Something happened (facts, not a request from the user):\n" + line.text)
            if not asked:
                self._waiting.insert(0, line)
                return
            self.last_commentary_at = self.clock()
            self._log({"kind": "partner-proactive", "event": line.event, "ref": line.ref, "eventT": line.wall,
                       "userBusy": False, "responseCreatedT": self.wall(),
                       **({"markTarget": line.mark} if line.mark else {})})
            return

    async def _watch(self) -> None:
        """Four times a second: waiting lines said when it is quiet; a renewal (GoAway, new tools) made when quiet."""
        while not self.closed:
            await asyncio.sleep(0.25)
            with contextlib.suppress(Exception):
                await self._deliver()
            quiet = (self._held is None and not self.speaking() and not self._calls and self.connected()
                     and not self._reconnecting)
            due = self._goaway_at is not None and self.clock() >= self._goaway_at - GOAWAY_MARGIN_S
            if (self._goaway_at is not None and (quiet or due)) or (self._tools_pending and quiet):
                why = "goaway" if self._goaway_at is not None else "tools"
                self._goaway_at, self._tools_pending = None, False
                with contextlib.suppress(Exception):
                    await self.reconnect(None, why=why)

    async def _stream_video(self) -> None:
        """The camera as video while Talk is open: the newest frame, at most VIDEO_FPS, grabbed off the loop; a frame
        already sent is not sent again."""
        from google.genai import types

        every = 1.0 / float(self.config.extra.get("video_fps", VIDEO_FPS))
        while not self.closed:
            started = self.clock()
            if self.connected() and self.video is not None:
                with contextlib.suppress(Exception):
                    seen = await self.video()
                    if seen is not None and seen[1] != self._last_frame_id and self.connected():
                        await self._realtime(video=types.Blob(data=seen[0], mime_type="image/jpeg"))
                        self._last_frame_id = seen[1]
                        self.frames_sent += 1
            await asyncio.sleep(max(0.05, every - (self.clock() - started)))

    # --- receiving -------------------------------------------------------------------------------------------
    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            event = await self._queue.get()
            yield event
            if event.kind == "closed":
                return

    def _emit(self, event: VoiceEvent) -> None:
        self._queue.put_nowait(event)
        for callback in self._callbacks:
            with contextlib.suppress(Exception):
                callback(event)

    def _log(self, record: dict) -> None:
        if self.noted is not None:
            with contextlib.suppress(Exception):
                self.noted(record)

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _receive(self, live, epoch: int) -> None:
        """The server's messages in order. `receive()` ends at each turn's end, so it is entered again; one that ends
        with nothing in it means the connection has closed."""
        why = "the connection closed"
        try:
            while not self.closed and epoch == self._epoch:
                got = False
                async for msg in live.receive():
                    got = True
                    await self.handle(msg)
                if not got:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            why = f"{type(exc).__name__}: {exc}"[:300]
        if self.closed or self._reconnecting or epoch != self._epoch:
            return
        self._connected.clear()
        self._emit(VoiceEvent("go_away", data={"reason": "close", "message": why}))

    async def handle(self, msg) -> None:
        """One LiveServerMessage, into VoiceEvents and the turn's state."""
        now = self.clock()
        update = msg.session_resumption_update
        if update is not None and update.new_handle and update.resumable is not False:
            self.resume_handle = update.new_handle
        if msg.go_away is not None:
            left = msg.go_away.time_left
            seconds = left.total_seconds() if hasattr(left, "total_seconds") else 60.0
            self._goaway_at = now + max(0.0, seconds)
            self._log({"kind": "partner-goaway", "timeLeftS": round(seconds, 1)})
        activity = msg.voice_activity
        if activity is not None and activity.voice_activity_type is not None:
            start = activity.voice_activity_type.name.endswith("START")
            self._emit(VoiceEvent("vad", data={"state": "start" if start else "end"}))
            if self.talk == "open" and start:
                self.n += 1
                self._last_user_at = now
                self.on_speech_started(self.n)
                self._spawn(self._open_turn(self.n))
            elif self.talk == "open":
                self._last_user_at = now
                self._next_cause = ("turn", self.n)
        sc = msg.server_content
        if sc is not None:
            if sc.input_transcription is not None and sc.input_transcription.text:
                piece = sc.input_transcription.text
                self._heard.append(piece)
                self._emit(VoiceEvent("input_transcript", text=piece, data={"interim": True, "delta": piece}))
            if sc.model_turn is not None:
                for part in sc.model_turn.parts or []:
                    blob = part.inline_data
                    if blob is not None and blob.data and (blob.mime_type or "").startswith("audio/"):
                        self._audio(blob.data)
            if sc.output_transcription is not None and sc.output_transcription.text:
                gen = self._generation()
                self._flush_heard()
                if not gen.get("suppressed"):
                    gen["transcript"] += sc.output_transcription.text
                    self._emit(VoiceEvent("output_transcript", text=sc.output_transcription.text,
                                          data={"responseId": gen["id"]}))
            if sc.interrupted:
                self._audio_until = now
                if self._gen is not None:
                    self._gen["status"] = "cancelled"
                    self._end_generation()
                self._emit(VoiceEvent("interrupted"))
            if sc.generation_complete and self._gen is not None:
                self._end_generation()
            if sc.turn_complete:
                self._flush_heard()
                if self._gen is not None:
                    self._end_generation()
                self._turn_done(msg.usage_metadata)
                self._emit(VoiceEvent("turn_complete", data={"reason": getattr(sc.turn_complete_reason, "name", None)}))
        elif msg.usage_metadata is not None:
            self._turn_done(msg.usage_metadata)
        if msg.tool_call is not None and msg.tool_call.function_calls:
            self._flush_heard()
            gen = self._generation()
            self._calls_arrived(list(msg.tool_call.function_calls), gen)
        if msg.tool_call_cancellation is not None:
            for call_id in msg.tool_call_cancellation.ids or []:
                self._withdrawn.add(call_id)
                self._log({"kind": "partner-call-withdrawn", "callId": call_id})

    def _flush_heard(self) -> None:
        """The user's words of the turn, filed once, when Gemini starts answering it (its transcription comes in pieces
        before)."""
        if not self._heard:
            return
        text, self._heard = "".join(self._heard).strip(), []
        if text:
            self._emit(VoiceEvent("input_transcript", text=text, data={"n": self._heard_n or self.n}))

    def _generation(self) -> dict:
        """The generation in progress, begun by the first thing of it that arrives."""
        if self._gen is None:
            if self._done is not None:
                self._log_response(self._done)  # its turn's usage never came: logged without it
                self._done = None
            self._gens += 1
            cause, n = self._next_cause
            # Never heard: the answer to a tap or Escape after a barge-in, and anything begun while the key is held (the
            # user is talking; the activity opening stops it).
            self._gen = {"id": f"gm_{self._gens}", "cause": cause, "n": n, "createdT": self.wall(), "firstAudioT": None,
                         "heardT": None, "audioS": 0.0, "transcript": "", "status": "completed",
                         "suppressed": cause == "tap" or self._held is not None}
            self._awaiting = None
            self._next_cause = ("unknown", n)
        return self._gen

    def _audio(self, pcm: bytes) -> None:
        gen = self._generation()
        if gen.get("suppressed"):
            return
        self._flush_heard()
        now = self.clock()
        seconds = len(pcm) / 2 / RATE_OUT
        if gen["firstAudioT"] is None:
            gen["firstAudioT"] = self.wall()
            if self._point and gen["cause"] in ("tool", "look"):
                targets, self._point = self._point, []
                with contextlib.suppress(Exception):
                    self.on_point(targets)
        gen["audioS"] += seconds
        self._spoke_in_turn.add(gen["n"])
        self._audio_until = max(self._audio_until, now) + seconds
        self._last_audio_at = now
        self._emit(VoiceEvent("audio", audio=pcm, rate=RATE_OUT))

    def _end_generation(self) -> None:
        gen, self._gen = self._gen, None
        if gen is None:
            return
        self._done = gen
        self._emit(VoiceEvent("generation_complete", data={"responseId": gen["id"], "status": gen["status"]}))

    def _turn_done(self, usage) -> None:
        gen, self._done = self._done, None
        facts = usage_facts(usage) if usage is not None else None
        if gen is not None:
            self._log_response(gen, facts)
        if facts is not None:
            usd = usage_usd(facts, self.config.model)
            self._emit(VoiceEvent("usage", data={"seconds": None, "tokens": facts, "usd": usd,
                                                 "responseId": gen["id"] if gen else None,
                                                 "cause": gen["cause"] if gen else None}))

    def _log_response(self, gen: dict, usage: dict | None = None) -> None:
        heard = gen.get("heardT") or gen.get("firstAudioT")
        end = heard + gen["audioS"] if heard is not None and gen["audioS"] else None
        self._log({"kind": "partner-response", "responseId": gen["id"], "n": gen["n"], "cause": gen["cause"],
                   "firstAudioT": heard if not gen.get("suppressed") else None, "firstChunkT": gen["firstAudioT"],
                   "audioEndT": end, "cancelled": gen["status"] == "cancelled" or bool(gen.get("suppressed")),
                   "status": "cancelled" if gen.get("suppressed") else gen["status"], "transcript": gen["transcript"],
                   "usage": usage or {}, **({"usd": round(usage_usd(usage, self.config.model), 6)} if usage else {})})

    async def _open_turn(self, n: int) -> None:
        """Open mic: the user began speaking; what was seen goes in while they speak (best effort: Gemini answers when
        its own detection ends the turn)."""
        await asyncio.sleep(EARLY_WAIT_S)
        seen = self.turn_items(n)
        with contextlib.suppress(Exception):
            await self._perceived(n, seen)
        self._heard_n = n
        self._log({"kind": "partner-turn", "n": n, "how": "open", "releasedT": None, "committedT": None,
                   "responseCreatedT": None, **seen.facts})

    # --- tools -------------------------------------------------------------------------------------------------
    def _calls_arrived(self, calls: list, gen: dict) -> None:
        batch = []
        for fc in calls:
            call_id, name = fc.id or "", fc.name or ""
            if not call_id or call_id in self._seen_calls:
                continue
            self._seen_calls.add(call_id)
            args = dict(fc.args or {})
            event = VoiceEvent("tool_call", call_id=call_id, name=name, args=args)
            self._emit(event)
            key = (name, json.dumps(args, sort_keys=True, default=str))
            first = self._call_keys.get(key)
            if first is not None and not first.done():
                # Gemini's documented repeat of a call still running: answered with the first one's result.
                self._log({"kind": "partner-call-repeated", "callId": call_id, "name": name})
                task = asyncio.ensure_future(self._repeat(first))
            else:
                task = asyncio.ensure_future(self._run_tool(event))
                self._call_keys[key] = task
            self._calls[call_id] = task
            batch.append((call_id, name, task))
        if batch:
            self._spawn(self._answer(batch, gen["n"], self._epoch))

    @staticmethod
    async def _repeat(first: asyncio.Task) -> dict:
        result = await asyncio.shield(first)
        return {**{k: v for k, v in result.items() if not k.startswith("_")},
                "note": "the same call was already running; this is its result. Do not call it again."}

    async def _run_tool(self, call: VoiceEvent) -> dict:
        spec = next((t for t in self.config.tools if t.name == call.name), None)
        started, t0 = self.wall(), time.perf_counter()
        try:
            result = await spec.handler({**call.args, "_call_id": call.call_id}) if spec is not None else \
                {"ok": False, "error": f"there is no {call.name} tool"}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = {"ok": False, "error": "something went wrong in the app",
                      "_log": {"error": f"{type(exc).__name__}: {exc}"[:300]}}
        result = result if isinstance(result, dict) else {"result": result}
        ms = round((time.perf_counter() - t0) * 1000)
        log = result.pop("_log", {}) if isinstance(result.get("_log"), dict) else {}
        self._log({"kind": "partner-tool", "n": self.n, "callId": call.call_id, "name": call.name,
                   "args": json.dumps(call.args, ensure_ascii=False)[:500], "startT": started, "endT": self.wall(),
                   "ok": result.get("ok", "error" not in result),
                   **({"error": result["error"]} if result.get("error") else {}),
                   "screenChange": False, "reportT": None, "confirmed": None, **log})
        self._emit(VoiceEvent("tool_result", call_id=call.call_id, name=call.name,
                              result={k: v for k, v in result.items() if k != "_look"}, data={"ms": ms}))
        return result

    async def _answer(self, batch: list[tuple[str, str, asyncio.Task]], n: int, epoch: int) -> None:
        """One tool_call message's results back together, each never empty; a fresh look first when a tool changed the
        screen; INTERRUPT once the current words have played (the user waits for it), SILENT when a newer turn has
        begun or the calls only pointed after the answer was spoken. A progress line while they run long."""
        from google.genai import types

        self._rounds += 1
        began, progress = self.clock(), 0
        try:
            pending = {task for _, _, task in batch}
            while pending:
                _, pending = await asyncio.wait(pending, timeout=0.1)
                if (pending and progress < PROGRESS_LINES and self.clock() - began >= PROGRESS_AFTER_S
                        and not self.audible() and self._held is None and n == self.n and epoch == self._epoch
                        and self.config.extra.get("progress", True)):
                    progress += 1
                    facts = self.running_facts() or "the tool you called"
                    self._log({"kind": "partner-progress", "n": n, "facts": facts, "line": progress})
                    await self._ask("progress", n, "In one short sentence, under 12 words, tell the user what is still "
                                    "being done. Do not say anything is done or shown, and call no tool: it is still "
                                    "running, and its result comes to you.", "Still running in Hooke: " + facts + ".")
        finally:
            self._rounds -= 1
            for call_id, _, _ in batch:
                self._calls.pop(call_id, None)
        responses, looks, looked = [], [], False
        for call_id, name, task in batch:
            try:
                result = task.result()
            except BaseException as exc:  # noqa: BLE001 - a cancelled or broken handler is said as such
                result = {"ok": False, "error": f"the tool did not finish ({type(exc).__name__})"}
            look = result.get("_look") if isinstance(result, dict) else None
            points = result.get("_point") if isinstance(result, dict) else None
            if isinstance(points, list) and points:
                self._point = [str(t) for t in points][:4]
            if isinstance(look, dict) and (look.get("images") or look.get("text")):
                looks.append(look)
                looked = True
            if call_id in self._withdrawn:
                self._log({"kind": "partner-output-dropped", "callId": call_id, "why": "Gemini withdrew the call"})
                continue
            responses.append(types.FunctionResponse(id=call_id, name=name, response=payload(result)))
        if epoch != self._epoch or not self.connected():
            # A connection since renewed may not know these calls: what they did goes in as context instead.
            for call_id, name, task in batch:
                self._log({"kind": "partner-output-dropped", "callId": call_id, "why": "the connection was renewed"})
            if responses and await self._wait_connected(RECONNECT_WAIT_S):
                with contextlib.suppress(Exception):
                    await self._text(item_text("Tools that finished across a reconnect (context only): " + json.dumps(
                        [{"tool": r.name, **r.response} for r in responses], ensure_ascii=False, default=str)[:4000]))
            return
        try:
            for look in looks:
                if look.get("text"):
                    await self._text(item_text(str(look["text"])))
                for jpeg, label in look.get("images") or []:
                    await self._image(jpeg, label)
        except Exception:
            pass
        if not responses:
            return
        pointing_only = all(name == "mark" for _, name, _ in batch) and n in self._spoke_in_turn
        if n != self.n or self._held is not None or pointing_only:
            scheduling = types.FunctionResponseScheduling.SILENT
        else:
            end = self.clock() + RESULT_QUIET_WAIT_S
            while self.audible() and self.clock() < end and self._held is None and n == self.n:
                await asyncio.sleep(0.05)
            newer = n != self.n or self._held is not None
            scheduling = (types.FunctionResponseScheduling.SILENT if newer
                          else types.FunctionResponseScheduling.INTERRUPT)
        for response in responses:
            response.scheduling = scheduling
        if scheduling != types.FunctionResponseScheduling.SILENT:
            self._next_cause = ("look" if looked else "tool", n)
        try:
            await self._live.send_tool_response(function_responses=responses)
            if scheduling != types.FunctionResponseScheduling.SILENT:
                self._awaiting = self.clock()
        except Exception as exc:
            self._log({"kind": "partner-output-dropped", "callIds": [r.id for r in responses],
                       "why": f"{type(exc).__name__}: {exc}"[:300]})


# --- one real session without a browser (the paid smoke test) -------------------------------------------------------
async def probe(model: str = MODEL, *, image: bytes | None, speech: bytes, rate: int = 24000,
                api_key: str | None = None, connect=None, timeout: float = 30.0, tool_s: float = 0.3) -> dict:
    """One held turn through GeminiPartnerSession itself: a tool, the brief, a labelled picture and the 1 fps video
    (when there is a picture), spoken words in an activity, the tool's call answered, then the spoken answer (the paid
    smoke test, and the app's own check of the option, model_checks). Returns the events in order, the
    calls, what was heard and said by cause, the usage and the times (ms) from activity_end to the tool call and to the
    first audio, and from the tool's result to the first audio of the answer."""
    from ..prompts import PARTNER_GEMINI

    calls = []

    async def outline_cells(args: dict) -> dict:
        calls.append(args)
        await asyncio.sleep(tool_s)
        return {"ok": True, "summary": "outlined 7 of them in the live view", "count": 7}

    tool = ToolSpec("outline_cells", "Outline and count what the user names in the camera picture (cells, nuclei, a "
                    "stoma) with the app's outliner. Always use it to outline or count.",
                    {"type": "object", "properties": {"what": {"type": "string"}}, "required": ["what"]}, outline_cells)
    session = GeminiPartnerSession(VoiceConfig(model=model, system_prompt=PARTNER_GEMINI, tools=[tool], input_rate=rate),
                                   api_key=api_key, talk="hold", connect=connect)
    log: list[dict] = []
    session.noted = log.append
    seen: list[VoiceEvent] = []
    session.on(seen.append)
    frames = iter(range(10_000))

    async def frame():
        return image, next(frames), [0, 0, 1, 1]
    if image is not None:
        session.video = frame
    session.seed_brief("Project Leaf: one sample of stained nuclei; nothing counted yet.", "r1")
    out: dict[str, Any] = {}
    try:
        await session.start()
        await asyncio.sleep(1.2)  # a video frame or two in
        await session.talk_down(1)
        await asyncio.sleep(MIN_HOLD_S + 0.05)
        await session.early(1, note="Turn 1. Screen now: the live view, nothing else open.")
        if image is not None:
            await session.early(1, image=(image, "Camera, frame 1: what the microscope showed when the user pressed "
                                                 "talk"))
        step = int(rate * 0.04) * 2
        for i in range(0, len(speech), step):
            await session.send_audio(speech[i:i + step])
            await asyncio.sleep(0.04)
        await session.talk_up(1)
        ended_m, ended_w = time.monotonic(), time.time()
        deadline = ended_m + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            responses = [r for r in log if r["kind"] == "partner-response"]
            if any(r["cause"] in ("tool", "look") and r["transcript"] for r in responses):
                break
        await asyncio.sleep(0.5)
    finally:
        await session.close()
    kinds = []
    for e in seen:
        if not kinds or kinds[-1] != e.kind:
            kinds.append(e.kind)
    responses = [r for r in log if r["kind"] == "partner-response"]
    tool_end = next((r["endT"] for r in log if r["kind"] == "partner-tool"), None)
    first = next((r["firstChunkT"] for r in responses if r.get("firstChunkT")), None)
    answer = next((r["firstChunkT"] for r in responses if r["cause"] in ("tool", "look") and r.get("firstChunkT")), None)
    call_at = next((e.at for e in seen if e.kind == "tool_call"), None)
    out.update(events=kinds, calls=calls, heard=" ".join(e.text for e in seen if e.kind == "input_transcript"
                                                         and not e.data.get("interim")),
               said=[(r["cause"], r["transcript"]) for r in responses], frames=session.frames_sent,
               toolCallMs=round((call_at - ended_m) * 1000) if call_at else None,
               firstAudioMs=round((first - ended_w) * 1000) if first else None,
               resultToAudioMs=round((answer - tool_end) * 1000) if answer and tool_end else None,
               usage=[r.get("usage") for r in responses if r.get("usage")],
               usd=round(sum(r.get("usd") or 0 for r in responses), 5), log=log)
    return out
