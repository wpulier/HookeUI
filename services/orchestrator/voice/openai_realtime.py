"""OpenAI `gpt-realtime-2.1` as the one-brain voice: it hears, sees and acts in one context (option VOICE).

GPT-Live (voice/openai_live.py) hears and speaks and hands requests to the brain by id; this model takes images and
calls the app's tools itself, with the user's own words, reads their results and speaks, in one conversation. The
studio side is the partner contract (docs/architecture/seeing-and-acting.md, "The one-brain loop").

Transport, as GPT-Live's WebRTC link: the browser's SDP offer and the session's config are posted by this server, which
keeps the API key, to `POST /v1/realtime/calls` (the SDK's `client.realtime.calls.create`); the SDP answer goes back to
the browser and the call id comes from the `Location` header. Audio flows browser <-> OpenAI over WebRTC (its echo
cancelling, and its truncation of unplayed audio on an interruption). This server attaches the sideband
`wss://api.openai.com/v1/realtime?call_id=...` (`client.realtime.connect(call_id=...)`), which receives the call's
events and sends everything the session needs: session.update, conversation items, responses, tool outputs.

What the session does, for the route and studio to treat it like the other voices (the same `VoiceEvent`s):
  - hold to talk (TALK "hold", turn detection off): `talk_down` clears the input buffer (and stops the voice when it is
    speaking: response.cancel, then output_audio_buffer.clear); `talk_up` adds the turn's perception (one kept text note
    and one image item) and commits the buffer and asks for a response at once, nothing fetched after the release;
    `talk_cancel` discards it. Open mic (TALK "open") uses semantic_vad with interruption, and the perception goes in
    when the server commits the user's speech;
  - per turn, the images of the turn before are deleted (conversation.item.delete); the text notes stay, so "go back to
    what we were looking at" is answered from them, and a reconnect is seeded with them;
  - the Project brief is one system item, replaced (deleted and made again) when its revision changes;
  - a function call runs its ToolSpec's handler; its output goes back (function_call_output), with a fresh look when
    the handler returns one (act, then look), and the model is asked to go on once every call of that response is
    answered; a tool still running after a moment gets one spoken progress line (no long silences);
  - what happens unasked (a researcher finished, a job's result, a long tool done, the watcher's events, a reconnect)
    goes on one bus (voice/events.py) that this session alone consumes: nothing is said while the key is held, a turn
    is pending or its audio still plays; results due together are said in one response, results first; a result is
    owed until its playback reached its end (output_audio_buffer.stopped), so one cut off comes back once at the next
    pause and a second cut retires it into context; progress is its own channel, idle only and spaced; context is a
    silent item. `proactive`, `deliver`, `tell_progress` and `inject` publish to it;
  - the 60-minute limit or a lost connection: `reconnect` makes a new call (the browser's new offer) or reattaches the
    sideband, seeded with the recent conversation, the brief and the per-turn notes, as the same session object.

Event names and shapes as they worked against the real API on 2026-10-29, and the latencies measured, are in
docs/architecture/seeing-and-acting.md ("The one-brain loop").
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

from ..untrusted import block, inline
from .base import VoiceConfig, VoiceEvent, VoiceSession
from .events import Bus, Event

MODEL = "gpt-realtime-2.1"  # option VOICE's one-brain choice (catalog/models/gpt-realtime-2-1)
RATE = 24000
# The user's words for the chat and the tools' checks of what was asked (input audio transcription; a fixed role).
TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
# WebRTC audio is still in flight when the key is released: the commit waits this long so the last word is in the
# buffer (the studio keeps the track on a little longer). Measured on the journey bench, not guessed beyond this.
RELEASE_TAIL_S = 0.15  # the user's last word kept whole (the spec, 29 Oct: no design choice made for a millisecond number)
MIN_HOLD_S = 0.25  # a press shorter than this is a tap: nothing to commit, so no answer
IDLE_S = 1.5  # nobody has spoken for this long: a line nobody asked for may be said
# The bus's consumer (`_consume`): results due within COALESCE_S of each other are said together, in one response (but
# a stream of them is not held past COALESCE_MAX_S), at most BATCH_MAX in a response; it looks every WATCH_EVERY_S.
COALESCE_S = 0.12
COALESCE_MAX_S = 0.5
BATCH_MAX = 4
WATCH_EVERY_S = 0.1
UNPROMPTED = ("proactive", "think", "progress", "prep", "opener")  # the bus's own responses: an interrupting event may stop them
RESPONSE_WAIT_S = 3.0  # a response whose end never comes is cancelled and let go after this (the gate never blocks)
RECONNECT_WAIT_S = 10.0  # a release during a reconnect waits this long for the connection to come back
# Progress while the user's own tool runs (a tool round): at most one line, and only once the voice has said nothing
# for PROGRESS_AFTER_S with the tool still running, so silence that would feel broken is filled and nothing else is.
# The smoke run of 29 Oct (judged 1 of 3, "robotic"): two lines from 1 s in, each cut off mid-sentence, then the result.
# If the result lands before the line is heard starting, the line is let go unheard and the result is said instead;
# one already playing is heard to its end. Its outputs then going on as "still running" are not said again.
PROGRESS_AFTER_S = 5.0
# After the voice's own lead-in ("Outlining that now.") the wait is already said: a line comes only if it runs long.
# Its row shows the work meanwhile (Will's rule of 2026-10-11: one acknowledgement, its row, one outcome).
PROGRESS_AFTER_LEADIN_S = 20.0
# A hand-off with no lead-in (will-alive "slow", 30 Oct: a 15 s web search said nothing until its outcome, judged "no
# brief acknowledgment that the search was under way"): one line on what Hooke is doing right now, from its brain's live
# progress, once the voice has been quiet HANDOFF_PROGRESS_AFTER_S and one of its steps has run HANDOFF_STEP_S (long
# work: a web search 11-16 s, every cell outlined 5-20 s), or once quiet HANDOFF_THINKING_S with none. Quick work says
# nothing before its outcome (the 30 Oct journeys: most hand-offs ended in 5-7 s, every find, zoom, Tab and window step
# under 2.5 s; lead-ins before quick work were judged "preliminary narration").
HANDOFF_PROGRESS_AFTER_S = 5.5
HANDOFF_STEP_S = 3.5
HANDOFF_THINKING_S = 9.0
# Words for that line: what is being done right now, as the voice's own ("I"), from the facts alone.
HANDOFF_PROGRESS_WORDS = ("In one short sentence (under 10 words), say as yourself (\"I\") what you are doing for this "
                          "request right now, in the user's words, from these facts alone: a step's own words, when it "
                          "has them, say what it is doing now (a tool's description says only what it can do); only "
                          "what is being done, never what it may find, that it is done, or what comes next; no tool's "
                          "name and no \"Hooke\". The user has heard nothing about it yet.")
PROGRESS_LINES = 1
# A spoken line's tokens include its reasoning: at 100 every progress line from 29 Oct was cut off mid-sentence. Its
# length is set by its instructions; this only stops a runaway.
PROGRESS_TOKENS = 400
# Words for any progress line (a tool round's and a job's): what is under way, as a colleague says it mid-task.
PROGRESS_WORDS = ("In one short sentence (under 12 words), say what Hooke is doing now or waiting on, in the user's "
                  "words, the way a colleague would mid-task (\"Still running the three models.\"). Only what it is "
                  "doing, never what it may find, no tool's name and no \"segmentation\"; how long only if it has "
                  "been a while.")
# A job behind the voice (think, a slow tool): a line on what is under way once the voice has been quiet this long, then
# another only when what it is doing has changed, at least THINK_PROGRESS_EVERY_S later, THINK_PROGRESS_LINES in all.
THINK_PROGRESS_AFTER_S = 6.0
THINK_PROGRESS_EVERY_S = 15.0
THINK_PROGRESS_LINES = 2  # think-check-2: three a task, two tasks at once, was judged "repeatedly narrated"
TOOL_ROUNDS = 8  # tool rounds one user turn may chain (a model calling tools forever is stopped)
NOTES_KEPT = 12  # per-turn text notes kept for a reconnect's seed
CLOSE_STEP_S = 2.0
AUDIO_TOKEN_S = 0.05  # one assistant audio token is 50 ms of speech (OpenAI's realtime pricing notes)
ACK_TOKENS = 200  # "On it." is a few words; the cap counts its reasoning too, so it is never cut off
PREP_WAIT_S = 4.0  # think's result prepared out of band at most this long, else the backend's own words
PREP_TOKENS = 900  # room for an explanation asked for in full, its reasoning included: it lands whole, never cut
# A result's words follow its receipt (voice/hands.py): "done" is Hooke's verified receipt, never the voice's judgement.
RECEIPT_RULES = {
    "verified": "Hooke checked that it happened: say the outcome first, plainly (\"Done, 345 nuclei outlined.\").",
    "failed": "It did not happen: say so plainly, why in a few words from its own, and the next thing to try.",
    "uncertain": "Hooke could not check all of it: say what is known and what is not; never say it is done.",
    "running": "It is still under way: say in a few words what is happening; never say it is done or found.",
}
# It talks first (Will's feel check, 29 Oct: HeyClicky "literally talks to you FIRST"): the instruction for its first
# line as Talk connects. The words are the voice's own, from the pictures and the Project it was just given.
OPENER_RULE = ("Talk has just connected and the user has not spoken yet. Speak first, once, in one or two short "
               "sentences: what is on the stage right now, as specifically as the pictures and the Project let you say "
               "it, and the one obvious next thing, offered as a question (the shape: \"That's onion epidermis; the "
               "nuclei are easy to see. Want me to outline them?\"). Say it first, then call mark on what you named, "
               "without saying that you point. "
               "With nothing on the stage, say in one sentence how to start. No greeting on its own, no list of what "
               "you can do.")
# Decide silently first (option PARTNER_SPEAKS "after-deciding", the default): a user's turn is answered first in text
# alone, so nothing is said before work is handed over (the 30 Oct journeys on b1e2727, three rounds of prompt rules
# later: "I'm checking the current tabs to find the measurement table, then I'll open it.", "Starting the experiment…"
# for a plan only drafted, each at 0.8-1.3 s, before do_in_hooke; judged "preliminary narration"). With a tool called,
# its words are let go unsaid and the outcome is said after it; with none (or only marks), its words are said, exactly,
# by one audio response (`_say_decided`). "as-it-decides" answers in speech at once, as before.
SPEAKS_AFTER_DECIDING = "after-deciding"
DECIDE_RULE = ("What you write in this reply is said aloud word for word, unless you call a tool other than mark: then "
               "nothing of it is said, and the tool's outcome is said after it. Write only what you would say, in "
               "plain spoken sentences.")
# The audio response that says a decided answer: its own context (only those words), its words added to the conversation
# as the one assistant message of that answer; short instructions, so its first audio comes as soon as it can.
SAY_DECIDED = ("You are the user's microscope partner, speaking. Say exactly the words you are given, once, as "
               "yourself, naturally, and nothing else.")
# Over WebRTC a response's audio plays on after its response.done; without the buffer's "stopped" since, a key press
# this soon after it still stops the voice (the studio mutes its audio element at once too).
AUDIBLE_AFTER_DONE_S = 20.0


def spoken(text: str) -> str:
    """Written words as they are said: markdown's marks (emphasis, code, headings, list bullets) taken out, one line."""
    text = re.sub(r"(\*\*|__|`)", "", str(text or ""))
    text = re.sub(r"^\s{0,3}(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)", "", text, flags=re.M)
    return " ".join(text.split())


def best_effort() -> str:
    """The event id of a step whose refusal is no failure (its error is logged, not said)."""
    return f"be_{uuid.uuid4().hex[:20]}"


def item_id(prefix: str = "hk") -> str:
    """A client-chosen item id (at most 32 characters), so an item can be deleted without waiting for its echo."""
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def text_item(role: str, text: str, iid: str | None = None) -> dict:
    kind = "output_text" if role == "assistant" else "input_text"
    return {"id": iid or item_id(), "type": "message", "role": role, "content": [{"type": kind, "text": text}]}


def image_item(images: list[tuple[bytes, str]], iid: str | None = None, detail: str = "auto") -> dict:
    """One user item of labelled images: each label as text, then its picture as a base64 data URL."""
    content: list[dict] = []
    for jpeg, label in images:
        if label:
            content.append({"type": "input_text", "text": label})
        content.append({"type": "input_image", "detail": detail,
                        "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()})
    return {"id": iid or item_id("im"), "type": "message", "role": "user", "content": content}


def usage_facts(usage: dict | None) -> dict:
    """response.done's usage as the bench and the meter read it: tokens by kind."""
    usage = usage or {}
    i, o = usage.get("input_token_details") or {}, usage.get("output_token_details") or {}
    return {"inText": i.get("text_tokens") or 0, "inAudio": i.get("audio_tokens") or 0,
            "inImage": i.get("image_tokens") or 0, "inCached": i.get("cached_tokens") or 0,
            "outText": o.get("text_tokens") or 0, "outAudio": o.get("audio_tokens") or 0}


def usage_usd(facts: dict, model: str = MODEL) -> float:
    """List price of one response's tokens (research_worker.budget.PRICES). Cached input has a lower price not read
    here: it is counted at the full price, so the meter can only over-count it."""
    from research_worker.budget import PRICES

    price = PRICES[model]
    rates = {"inText": price["in"], "inAudio": price["audio_in"], "inImage": price["image_in"], "outText": price["out"],
             "outAudio": price["audio_out"]}
    return sum(facts.get(k, 0) * rates[k] for k in rates) / 1e6


@dataclass
class Perception:
    """What one turn saw, ready when the key is released: a short text note (kept), labelled images (deleted at the
    next turn) and facts for the log."""
    note: str = ""
    images: list[tuple[bytes, str]] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)  # an image's label -> its detail ("low" for the window)


class SdkSideband:
    """The SDK's realtime connection as raw JSON both ways: events go out exactly as built here (no client-side
    reshaping of fields the SDK's types may not know yet), and come in as dicts."""

    def __init__(self, manager) -> None:
        self.manager, self.conn = manager, None

    async def open(self) -> "SdkSideband":
        self.conn = await self.manager.__aenter__()
        return self

    async def send(self, event: dict) -> None:
        await self.conn.send_raw(json.dumps(event))

    async def recv(self) -> dict:
        return json.loads(await self.conn.recv_bytes())

    async def close(self) -> None:
        if self.conn is not None:
            await self.manager.__aexit__(None, None, None)


@dataclass
class Ask:
    """One response.create waiting for its turn at the gate (`RealtimeSession._gate`): the API takes one response at a
    time, so every response the session asks for goes through one queue and is checked again just before it is sent."""
    cause: str  # turn | typed | tool | look | progress | proactive | reconnect
    n: int  # the user's turn it belongs to
    response: dict
    epoch: int  # the call it was asked on
    still: Callable[[], bool] | None = None  # checked just before sending: False drops it
    prepare: Callable[[], Awaitable[None]] | None = None  # run once just before (a proactive line's facts and mark)
    sent: Callable[[], None] | None = None
    dropped: Callable[[str], None] | None = None
    tries: int = 0


USER_CAUSES = ("turn", "typed")  # the user's own turns: first in the queue, and never dropped for waiting
# Responses that answer the user (their turn, a tool round, the acknowledgement, a turn lost on a reconnect): one waiting
# at the gate is the voice thinking (`phase`); a line nobody asked for waiting for quiet is not.
ANSWER_CAUSES = ("turn", "typed", "tool", "look", "ack", "reconnect", "say")
# Tools whose work is Hooke's, not the voice's: their wait shows as Hooke working, with the hand-off's row (presence.py).
HANDED_TOOLS = frozenset({"do_in_hooke", "think"})


class RealtimeSession(VoiceSession):
    def __init__(self, config: VoiceConfig, *, api_key: str | None = None, talk: str = "hold",
                 create_call: Callable[[str, dict], Awaitable[tuple[str, str]]] | None = None,
                 open_sideband: Callable[[str], Awaitable[Any]] | None = None,
                 hangup: Callable[[str], Awaitable[None]] | None = None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 bus: Bus | None = None, said=None) -> None:
        """`create_call(offer_sdp, session) -> (answer_sdp, call_id)`, `open_sideband(call_id) -> sideband` (send(dict),
        recv() -> dict, close()), `hangup(call_id)`: the SDK's by default; tests pass fakes. `bus`: the events this
        session says (voice/events.py), a new one when None; one kept by its caller and handed to the next session keeps
        what is owed. `said`: the brain's record of outcomes said (narrator.Said), claimed when they are heard."""
        self.config = config
        self.talk = talk  # "hold" | "open" (option TALK)
        self.link = "webrtc"
        self.browser_client = None  # bound by the accepted Studio voice route
        self._api_key = api_key
        self._create_call, self._open_sideband, self._hangup = create_call, open_sideband, hangup
        self.clock, self.wall = clock, wall
        self.noted: Callable[[dict], None] | None = None  # the interaction log (the route sets it)
        self.answer_sdp: str | None = None
        self.call_id: str | None = None
        self.session_id: str | None = None
        self._sb = None
        self._recv_task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None
        self._gate_task: asyncio.Task | None = None
        self._queue: asyncio.Queue[VoiceEvent] = asyncio.Queue()
        self.closed = False
        self.termination = ""
        self.usage_seconds = None  # billed by tokens (usage events), not seconds
        self.last_commentary_at = 0.0
        self.spoken_actions: set[str] = set()
        self.opened_at = clock()
        # Seeds a new call (a reconnect) with the conversation as it is then: (role, text) pairs.
        self.history: Callable[[], list[tuple[str, str]]] = lambda: list(config.history)
        # The partner's own hooks (voice/partner.py): the turn's perception, ready now (never fetched after release).
        self.turn_items: Callable[[int], Perception] = lambda n: Perception()
        self.on_speech_started: Callable[[int], None] = lambda n: None  # open mic: the user began; capture now
        # What a line the app asks to be said is about, when its own facts say so (a researcher that just finished):
        # tag_notice(text) -> {"event", "ref", "mark", "before"} or None.
        self.tag_notice: Callable[[str], dict | None] = lambda text: None
        # Points at what a tool just put on screen, as the answer about it starts to be heard (partner: marks).
        self.on_point: Callable[[list], None] = lambda targets: None
        # --- conversation state ---
        self.n = 0  # the user's turn number (talk presses, or speech in open mic)
        self._held: int | None = None
        self._held_at = 0.0
        self._held_epoch = 0
        self._released_at = 0.0
        self._asks: list[Ask] = []  # responses waiting for the gate, one at a time
        self._gate_wake = asyncio.Event()
        self._requested: dict | None = None  # a response.create sent, not yet created
        self._unsay: int | None = None  # a turn whose progress line, asked for, is let go as it is created
        self._opened_line = False  # its first line as Talk connects was offered (once per Talk session)
        self._response: str | None = None  # the response in progress
        self._causes: dict[str, dict] = {}  # response id -> {"cause", "n", "createdT"}
        self._transcripts: dict[str, str] = {}  # response id -> the assistant's words so far
        self._parts: dict[str, tuple] = {}  # response id -> the (item, content part) its last words came in
        self._speaking = False
        self._last_audio_at = 0.0
        self._last_user_at = 0.0
        self._last_done_at = 0.0
        self._audio_until = 0.0  # when the words being played now will end (monotonic), from the responses' audio tokens
        self._started_at: dict[str, float] = {}  # response id -> when its audio started (monotonic)
        self._out_audio: dict[str, int] = {}  # response id -> its audio tokens (50 ms each)
        self.heard: Callable[[int], None] = lambda n: None  # a real turn released: the studio's heard cue (partner)
        # The voice's own phase may have changed (voice/presence.py reads `phase()` and sends the studio its state).
        self.on_phase: Callable[[], None] = lambda: None
        self._heard_n: int | None = None  # a released turn taken, its answer not yet asked for: "heard"
        self._rounds_open = 0  # tool rounds being answered (`_finish_round`), between a call ending and the next response
        self.started_tools: frozenset[str] = frozenset({"think"})  # answered "started", said when they land (partner)
        self.running_facts: Callable[[], str] = lambda: ""  # what is still running (partner_tools), for progress lines
        # How a user's turn is answered (option PARTNER_SPEAKS, read at each turn): "after-deciding" (the default) or
        # "as-it-decides" (the partner binds it to the app's setting).
        self.speaks: Callable[[], str] = lambda: SPEAKS_AFTER_DECIDING
        # What the hand-off a do_in_hooke call started is doing now ({"facts", "stepS"}, or None: partner_tools).
        self.handoff_doing: Callable[[str], dict | None] = lambda call_id: None
        self.handed_late: Callable[..., bool] = lambda ctx, run: False  # a hand-off keeps its late steps (partner_tools)
        self._first_audio: dict[str, float] = {}
        self._calls: dict[str, asyncio.Task] = {}  # call id -> its handler's task
        self._seen_calls: set[str] = set()
        self._call_names: dict[str, str] = {}
        self._call_epoch: dict[str, int] = {}  # call id -> the call (epoch) it was made on
        self._round: dict[str, list[str]] = {}  # response id -> its calls
        self._rounds_this_turn = 0
        self._images: list[str] = []  # image items of the last turn: deleted at the next
        self._early_n: int | None = None  # the turn whose perception went in while its key was held
        self._turn_items: dict[int, list[str]] = {}  # turn -> its perception's item ids (a cancelled turn's are deleted)
        self._point: list = []  # targets (ids, or frame marks) to point at when the next answer is heard
        self._cancelled: set[int] = set()
        self._turn_notes: dict[int, list[str]] = {}
        self._brief: tuple[str, str] | None = None  # (item id, revision)
        self.brief_text = ""
        self.notes: list[str] = []  # each turn's short text note of what was seen
        self._prepared: dict[str, asyncio.Future] = {}  # think task -> its out-of-band words
        # --- the bus (voice/events.py): this session is its one consumer ---
        self.bus = bus if bus is not None else Bus(clock=clock, wall=wall)
        self.bus.adopt(self)
        self.said = said
        self._carried: dict[str, dict] = {}  # response id -> {"started", "words", "audioS", "done"} of one carrying events
        self._saying: asyncio.Task | None = None  # a response for the bus being made ready (its results worded)
        self._audible_cause: str | None = None  # what the audio playing now answers (its response's cause)
        # A result cut off twice is retired (told as context, never said a third time): the partner may add an Activity
        # line for it here.
        self.on_retired: Callable[[Event], None] = lambda event: None
        self._epoch = 0  # which call this is: each new call is the next
        self._connected = asyncio.Event()  # a sideband is open
        self._reconnecting = False
        self.reconnects = 0
        self._said_reconnect_at = -1e9
        self._late: set = set()

    # --- the session's config ------------------------------------------------------------------
    def session_config(self) -> dict:
        extra = self.config.extra
        if self.talk == "open":
            turn = {"type": "semantic_vad", "eagerness": extra.get("eagerness", "auto"),
                    "create_response": False, "interrupt_response": True}
        else:
            turn = None  # hold to talk: the app says when a turn starts and ends
        audio_in: dict[str, Any] = {"noise_reduction": {"type": extra.get("noise_reduction", "far_field")},
                                    "turn_detection": turn}
        if self.config.transcribe_input:
            audio_in["transcription"] = {"model": extra.get("transcribe", TRANSCRIBE_MODEL)}
        session: dict[str, Any] = {
            "type": "realtime", "model": self.config.model, "output_modalities": ["audio"],
            "audio": {"input": audio_in, **({"output": {"voice": self.config.voice}} if self.config.voice else {})},
            # "low": the model's default and its better answers and tool choices; "minimal" was chosen for first-audio
            # speed (0.82 s vs 1.10 s) and is undone (Will, 29 Oct: quality first, timing reported only).
            "reasoning": {"effort": extra.get("reasoning", "low")},
            "truncation": {"type": "retention_ratio", "retention_ratio": 0.8},
        }
        if self.config.system_prompt:
            session["instructions"] = self.config.system_prompt
        tools = self.tool_declarations()
        if tools:
            # Several calls in one response save a round trip; the tools are safe run together (partner_tools: one
            # researcher per turn, screen changes one after another).
            session["tools"], session["tool_choice"], session["parallel_tool_calls"] = tools, "auto", True
        return session

    def tool_declarations(self) -> list[dict]:
        return [{"type": "function", "name": t.name, "description": t.description, "parameters": t.parameters}
                for t in self.config.tools]

    async def update_tools(self, tools) -> None:
        """The tools changed (a capability turned on): the session is told, nothing else changes."""
        self.config.tools = list(tools)
        await self._send({"type": "session.update", "session": {"type": "realtime", "tools": self.tool_declarations()}})

    # --- lifecycle ---------------------------------------------------------------------------------
    def _client(self):
        from openai import AsyncOpenAI

        return AsyncOpenAI(api_key=self._api_key or os.environ["OPENAI_API_KEY"], max_retries=0)

    def _defaults(self) -> None:
        if self._create_call is not None and self._open_sideband is not None and self._hangup is not None:
            return
        client = self._client()

        async def create_call(offer: str, session: dict) -> tuple[str, str]:
            raw = await client.realtime.calls.with_raw_response.create(sdp=offer, session=session)
            location = raw.headers.get("location") or ""
            call_id = location.rstrip("/").rsplit("/", 1)[-1]
            if not call_id:
                raise RuntimeError("the realtime call was made but its id was not returned")
            return raw.text, call_id

        async def open_sideband(call_id: str):
            return await SdkSideband(client.realtime.connect(call_id=call_id)).open()

        self._create_call = self._create_call or create_call
        self._open_sideband = self._open_sideband or open_sideband
        self._hangup = self._hangup or (lambda call_id: client.realtime.calls.hangup(call_id))

    async def start(self, offer_sdp: str | None = None) -> None:
        if not offer_sdp:
            raise ValueError("the one-brain voice needs the browser's SDP offer (WebRTC)")
        self._defaults()
        await self._open_call(offer_sdp)
        self._watch_task = asyncio.create_task(self._watch())
        self._gate_task = asyncio.create_task(self._gate())
        self._emit(VoiceEvent("ready", data={"session_id": self.call_id, "link": "webrtc"}))

    async def _open_call(self, offer_sdp: str) -> None:
        """A new call: made, its sideband attached, then seeded. The previous call (if any) is the caller's to end."""
        answer, call_id = await self._create_call(offer_sdp, self.session_config())
        try:
            sb = await self._open_sideband(call_id)
        except BaseException:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._hangup(call_id), CLOSE_STEP_S)
            raise
        self.answer_sdp, self.call_id, self.session_id, self._sb = answer, call_id, call_id, sb
        self.closed = False
        self.opened_at = self.clock()
        self._epoch += 1
        self._recv_task = asyncio.create_task(self._receive())
        self._connected.set()
        await self._seed()

    async def _seed(self) -> None:
        """What a new call starts knowing: the recent conversation, the brief and the notes of what was seen."""
        self._images, self._requested, self._response, self._speaking = [], None, None, False
        # Calls of the old conversation are not this one's: their work goes on (and is told as it lands), but no
        # output is sent for a call id this conversation never had, and nothing waits on them.
        self._calls.clear()
        self._round.clear()
        for role, text in self.history()[-16:]:
            if text.strip():
                await self._send({"type": "conversation.item.create",
                                  "item": text_item(role if role in ("user", "assistant") else "system", text)})
        if self.notes:
            await self._send({"type": "conversation.item.create", "item": text_item(
                "system", "What was seen on earlier turns (context only):\n" + "\n".join(self.notes[-NOTES_KEPT:]))})
        if self.brief_text:
            revision = self._brief[1] if self._brief else ""
            self._brief = None
            await self.set_brief(self.brief_text, revision)

    async def close(self) -> None:
        if self.closed and self._sb is None:
            return
        self.closed = True
        for task in (self._watch_task, self._gate_task, self._saying):
            if task is not None:
                task.cancel()
        self._gate_wake.set()
        self._cut_off(None, "Talk ended")  # what was playing was not heard to its end: owed to the next session
        unresolved = await self._end_call(self._sb, self._recv_task, self.call_id)
        self._sb = self._recv_task = None
        self._connected.clear()
        self.answer_sdp = None
        self.termination = "confirmed" if not unresolved else "unconfirmed: " + "; ".join(unresolved)
        self._emit(VoiceEvent("closed", data={"reason": "client"}))
        self._phase_changed()

    async def _end_call(self, sb, task, call_id) -> list[str]:
        unresolved = []
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(task, CLOSE_STEP_S)
        if sb is not None:
            try:
                await asyncio.wait_for(sb.close(), CLOSE_STEP_S)
            except Exception:
                unresolved.append("its sideband didn't close")
        if call_id and self._hangup is not None:
            try:
                await asyncio.wait_for(self._hangup(call_id), CLOSE_STEP_S)
            except Exception as exc:  # an already-ended call refuses a hangup: it is ended either way
                if "not found" not in str(exc).lower() and "404" not in str(exc):
                    unresolved.append("its WebRTC call wasn't hung up")
        return unresolved

    async def reconnect(self, offer_sdp: str | None = None, *, why: str = "close") -> float:
        """A new call from the browser's new offer (the 60-minute limit, a call that ended, the studio's own new peer
        connection), or the sideband alone reattached to the same call. Returns the seconds it took. The same session
        object, so everything that holds it (researchers' delivery, the narrator) still reaches it.
          - Reattached: the call's conversation still holds everything (the brief, the images by their ids): nothing is
            seeded again.
          - A new call: made first, seeded, and only then the old one closed and hung up, in the background, so the new
            call never waits on the old one's end."""
        started = self.clock()
        self._reconnecting = True
        self._connected.clear()
        mid_speech = self._speaking
        try:
            if offer_sdp:
                old = (self._sb, self._recv_task, self.call_id)
                self._sb = self._recv_task = None
                self._cut_off(None, "a new call was made")  # the old call's audio is gone with it
                await self._open_call(offer_sdp)
                self._spawn(self._end_call(*old))
            else:
                task, self._recv_task = self._recv_task, None
                if task is not None:
                    task.cancel()
                sb, self._sb = self._sb, None
                if sb is not None:
                    self._spawn(self._quietly(sb.close()))
                self._sb = await self._open_sideband(self.call_id)
                self._recv_task = asyncio.create_task(self._receive())
                self._connected.set()
        finally:
            self._reconnecting = False
        self._gate_wake.set()
        self.reconnects += 1
        took = self.clock() - started
        self._log({"kind": "partner-reconnect", "reason": why, "closedT": self.wall() - took, "readyT": self.wall(),
                   "ms": round(took * 1000), "midSpeech": mid_speech, "newCall": bool(offer_sdp)})
        if mid_speech and self.clock() - self._said_reconnect_at > 60:  # one quiet line, not one per drop
            self._said_reconnect_at = self.clock()
            await self.proactive("The connection was renewed while you were speaking; say only: Reconnected, go on.",
                                 event="reconnect", ref=str(self.reconnects))
        return took

    @staticmethod
    async def _quietly(awaitable) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(awaitable, CLOSE_STEP_S)

    async def _wait_connected(self, seconds: float) -> bool:
        if self._connected.is_set() and self._sb is not None:
            return True
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._connected.wait(), seconds)
        return self._connected.is_set() and self._sb is not None and not self.closed

    # --- sending -----------------------------------------------------------------------------------
    async def _send(self, event: dict) -> None:
        if self._sb is None:
            raise ConnectionError("the realtime sideband is not open")
        await self._sb.send(event)

    async def add(self, item: dict) -> str:
        await self._send({"type": "conversation.item.create", "item": item})
        return item["id"]

    async def delete(self, iid: str) -> None:
        with contextlib.suppress(Exception):
            await self._send({"type": "conversation.item.delete", "item_id": iid, "event_id": best_effort()})

    def respond(self, cause: str, *, n: int | None = None, still: Callable[[], bool] | None = None,
                prepare: Callable[[], Awaitable[None]] | None = None, sent: Callable[[], None] | None = None,
                dropped: Callable[[str], None] | None = None, first: bool = False, **response) -> Ask:
        """Ask for a response: queued at the gate, which sends one at a time. The user's own turns go ahead of
        everything else (then `first`: an interrupting event); the rest are checked again just before sending (no key
        held, no newer turn, the same call)."""
        ask = Ask(cause, self.n if n is None else n, response, self._epoch, still, prepare, sent, dropped)
        if cause in USER_CAUSES or first:
            at = next((k for k, queued in enumerate(self._asks) if queued.cause not in USER_CAUSES), len(self._asks))
            self._asks.insert(at, ask)
        else:
            self._asks.append(ask)
        self._gate_wake.set()
        self._phase_changed()
        return ask

    def _deciding(self) -> dict:
        """A user's turn's response: in text alone when it decides silently first (option PARTNER_SPEAKS), so nothing
        is said before work is handed over; in speech at once otherwise."""
        try:
            silent = self.speaks() == SPEAKS_AFTER_DECIDING
        except Exception:
            silent = True
        if not silent:
            return {}
        return {"output_modalities": ["text"], "metadata": {"decide": "1"},
                "instructions": self.config.system_prompt + "\n\n" + DECIDE_RULE}

    def _why_not(self, ask: Ask) -> str | None:
        """Why a waiting response must not be sent now (None: it may)."""
        if ask.epoch != self._epoch:
            return "asked on a call that has since been replaced"
        if ask.n != self.n:
            return "the user has started a newer turn"  # that turn's answer reads what this one would have
        if ask.cause not in USER_CAUSES and self._held is not None:
            return "the user is holding the talk key"
        if ask.still is not None and not ask.still():
            return "no longer current"
        return None

    async def _gate(self) -> None:
        """The one sender of response.create: a response at a time (the API refuses a second), each checked again with
        nothing awaited between that check and claiming the slot."""
        while not self.closed:
            if not self._asks:
                self._gate_wake.clear()
                await self._gate_wake.wait()
                continue
            await self._free()
            if not await self._wait_connected(1.0):
                continue
            ask = self._asks[0]
            why = self._why_not(ask)
            if why is not None:
                self._asks.remove(ask)
                self._log({"kind": "partner-respond-dropped", "cause": ask.cause, "n": ask.n, "why": why})
                if ask.dropped is not None:
                    with contextlib.suppress(Exception):
                        ask.dropped(why)
                self._phase_changed()
                continue
            if ask.prepare is not None:
                prepare, ask.prepare = ask.prepare, None
                with contextlib.suppress(Exception):
                    await prepare()
                continue  # checked again: the user may have pressed talk while it was prepared
            if self._requested is not None or self._response is not None:
                continue
            self._asks.remove(ask)
            event_id = f"rc_{uuid.uuid4().hex[:20]}"
            meta = {**(ask.response.get("metadata") or {}), "cause": ask.cause, "n": str(ask.n)}
            self._requested = {**meta, "eventId": event_id, "since": self.clock(), "ask": ask}
            try:
                await self._send({"type": "response.create", "event_id": event_id,
                                  "response": {**ask.response, "metadata": meta}})
            except Exception:
                self._requested = None
                if ask.tries < 3:  # the sideband went away: sent once it is back
                    ask.tries += 1
                    self._asks.insert(0, ask)
                await asyncio.sleep(0.1)
                continue
            if ask.sent is not None:
                with contextlib.suppress(Exception):
                    ask.sent()

    async def _free(self) -> None:
        """Until the response in progress has ended. One whose end never comes (a lost event) is cancelled after
        RESPONSE_WAIT_S and let go: the queue is never blocked for good, and a user's turn is never skipped."""
        start = self.clock()
        while self._requested is not None or self._response is not None:
            if self._requested is not None and self.clock() - self._requested["since"] > RESPONSE_WAIT_S:
                self._requested = None  # asked, and nothing was made of it: none is coming
                self._phase_changed()
                continue
            if self.clock() - start > RESPONSE_WAIT_S:
                self._log({"kind": "partner-response-forced", "responseId": self._response})
                with contextlib.suppress(Exception):
                    await self._send({"type": "response.cancel", "event_id": best_effort()})
                end = self.clock() + 1.0
                while self._response is not None and self.clock() < end:
                    await asyncio.sleep(0.02)
                self._response = self._requested = None
                self._phase_changed()
                return
            await asyncio.sleep(0.02)

    def seed_brief(self, text: str, revision: str) -> None:
        """The brief a new call starts with (sent with the seed, not now)."""
        self.brief_text, self._brief = text, ("", revision)

    async def set_brief(self, text: str, revision: str) -> None:
        """The Project brief: one system item, replaced only when its revision changed."""
        if self._brief is not None and self._brief[1] == revision:
            return
        old, iid = self._brief, item_id("br")
        self._brief, self.brief_text = (iid, revision), text
        if old is not None and old[0]:
            await self.delete(old[0])
        await self.add(text_item("system", "The Project now (context only, kept current by the app):\n" + text, iid))

    # --- talking -----------------------------------------------------------------------------------
    def speaking(self) -> bool:
        return self._speaking or self._response is not None or self._requested is not None

    def phase(self) -> str:
        """What the voice itself is doing now, for its state on screen (voice/presence.py; W3 "alive and available"):
          - listening: a turn's key is held;
          - speaking: its words are audible (the output buffer's events, else the studio's measured voice-phase);
          - heard: a released turn was taken, its answer not yet asked for (the commit's tail);
          - thinking: an answer is being made with nothing audible: a response asked for or being generated, a user's
            answer waiting at the gate, or a tool of its own running or its round being answered. A hand-off's tool
            (`HANDED_TOOLS`) is not the voice thinking: it is Hooke's work, shown as working with its row;
          - quiet: none of these (idle, or Hooke's work alone in flight); off: the session closed."""
        if self.closed:
            return "off"
        if self._held is not None:
            return "listening"
        if self._speaking:
            return "speaking"
        if self._heard_n is not None:
            return "heard"
        if self._requested is not None or self._response is not None:
            return "thinking"
        if any(ask.cause in ANSWER_CAUSES for ask in self._asks):
            return "thinking"
        running = [cid for cid, task in self._calls.items() if not task.done()]
        if any(self._call_names.get(cid) not in HANDED_TOOLS for cid in running):
            return "thinking"  # a tool of its own (a closer look, a mark) runs
        if self._rounds_open and not running:
            return "thinking"  # every call answered: its outputs going in and the next response asked for
        return "quiet"

    def _phase_changed(self) -> None:
        with contextlib.suppress(Exception):
            self.on_phase()

    def studio_phase(self, phase: str) -> None:
        """The studio's own measured state of its audio (`voice-phase`): speaking or listening. With the sideband's
        output_audio_buffer events, a source of truth for whether the voice is audible."""
        now = self.clock()
        if phase == "speaking":
            rid = self._response or ""
            if rid and rid not in self._first_audio:
                self._first_audio[rid] = self.wall()
            if rid in self._carried and self._carried[rid]["started"] is None:
                self._carried[rid]["started"] = now
            self._last_audio_at = now
            if not self._speaking:
                self._speaking = True
                self._emit(VoiceEvent("vad", data={"state": "model-start"}))
                self._point_now()
        elif phase == "listening" and self._speaking:
            self._speaking = False
            self._last_audio_at = now
            self._emit(VoiceEvent("turn_complete", data={"reason": "quiet"}))
            self._gate_wake.set()
        self._phase_changed()

    def _point_now(self) -> None:
        """What a tool just put on screen is pointed at as the answer about it starts to be heard (no round trip
        through the model's mark tool: the bench's silent `look` response with only a mark in it, 2026-10-29)."""
        targets, self._point = self._point, []
        if targets and self._causes.get(self._response or "", {}).get("cause") in ("look", "tool", "think", "say"):
            with contextlib.suppress(Exception):
                self.on_point(targets)
        elif targets:
            self._point = targets  # not this answer (a progress line): the one about the result

    def _maybe_audible(self) -> bool:
        """A response ended moments ago and its audio's end was not reported: the browser may still be playing it."""
        return (self._last_done_at > self._last_audio_at and self.clock() - self._last_done_at < AUDIBLE_AFTER_DONE_S)

    async def talk_down(self, n: int | None = None) -> None:
        """The talk key went down: the voice stops if it is talking (barge-in), and listening starts afresh."""
        self.n = n if isinstance(n, int) else self.n + 1
        barge = self.speaking() or self._maybe_audible()
        self._held, self._held_at, self._held_epoch = self.n, self.clock(), self._epoch
        self._heard_n = None
        self._last_user_at = self.clock()
        self._log({"kind": "partner-talk", "n": self.n, "down": True, "barge": barge})
        self._phase_changed()
        for event in self.bus.withdraw("opener"):  # the user spoke first: its first line is let go unsaid
            self._event_log(event, "the user spoke first")
        if barge:
            await self.stop_speaking()
        with contextlib.suppress(ConnectionError):  # reconnecting: the release waits for it
            await self._send({"type": "input_audio_buffer.clear"})

    async def stop_speaking(self) -> None:
        cancel_at = self.wall()
        if self._response is not None or self._requested is not None:
            with contextlib.suppress(Exception):
                await self._send({"type": "response.cancel", "event_id": best_effort()})
        with contextlib.suppress(Exception):
            await self._send({"type": "output_audio_buffer.clear", "event_id": best_effort()})
        self._speaking = False
        self._audio_until = min(self._audio_until, self.clock())
        self._cut_off(None, "the voice was stopped")  # a result it was saying stays owed
        self._emit(VoiceEvent("interrupted"))
        self._phase_changed()
        self._log({"kind": "partner-barge", "n": self.n, "cancelT": cancel_at, "clearT": self.wall()})

    async def talk_up(self, n: int | None = None) -> bool:
        """The talk key was released: the turn's perception goes in, the buffer is committed and its answer queued at
        once (never skipped). False when nothing is asked: a tap, or a turn whose audio was in a call since replaced."""
        if self._held is None or (isinstance(n, int) and n != self._held):
            return False
        n, held_for, held_epoch = self._held, self.clock() - self._held_at, self._held_epoch
        self._held, self._released_at = None, self.clock()
        released = self.wall()
        self._log({"kind": "partner-talk", "n": n, "down": False})
        self._gate_wake.set()
        if held_for < MIN_HOLD_S:
            self._phase_changed()
            with contextlib.suppress(ConnectionError):
                await self._send({"type": "input_audio_buffer.clear"})
                await self._forget_turn(n)
            return False
        with contextlib.suppress(Exception):
            self.heard(n)  # a sign it heard you, at once: the studio's soft cue (never a word)
        self._heard_n = n  # taken: "heard" until its answer is asked for, then thinking
        self._phase_changed()
        try:
            seen = self.turn_items(n)  # as it was at key-down, taken now
            if not await self._wait_connected(RECONNECT_WAIT_S):
                self._log({"kind": "partner-turn-lost", "n": n, "why": "no connection came back"})
                return False
            if self._epoch != held_epoch:
                # Its audio went to a call that has since been replaced: said once, plainly.
                self._log({"kind": "partner-turn-lost", "n": n, "why": "a new call was made while the key was held"})
                self.respond("reconnect", n=n, tool_choice="none", instructions=self.config.system_prompt + (
                    "\n\nSay only, in your own words: the connection dropped for a moment, so please say that again."))
                return False
            await self._perceived(n, seen)
            await asyncio.sleep(RELEASE_TAIL_S)
            await self._send({"type": "input_audio_buffer.commit"})
            committed = self.wall()
            self._rounds_this_turn = 0
            self._last_user_at = self.clock()
            self.respond("turn", n=n, sent=lambda: self._log({
                "kind": "partner-turn", "n": n, "how": "hold", "releasedT": released, "committedT": committed,
                "responseCreatedT": self.wall(), **seen.facts}), **self._deciding())
            return True
        finally:
            if self._heard_n == n:  # asked for now (thinking), or lost (said, or nothing to answer)
                self._heard_n = None
            self._phase_changed()

    async def talk_cancel(self, n: int | None = None) -> None:
        if self._held is None:
            return
        n = self._held
        self._log({"kind": "partner-talk", "n": n, "down": False, "cancel": True})
        self._held = None
        self._gate_wake.set()
        self._phase_changed()
        with contextlib.suppress(ConnectionError):
            await self._send({"type": "input_audio_buffer.clear"})
            await self._forget_turn(n)

    async def early(self, n: int, *, note: str = "", image: tuple[bytes, str] | None = None, detail: str = "auto") -> bool:
        """Part of turn n's perception sent while its key is held, as soon as it is ready (the note at once, the
        camera and the window as each comes): the API prepares items as they arrive, so the answer starts sooner
        (the tuning micro-bench: 0.74 s from response.create with the pictures sent during the hold, 1.33 s after).
        False when the turn is over (cancelled, or a newer one) or the sideband is away: the release sends it."""
        if n != self.n or self._sb is None or n in self._cancelled:
            return False
        await self._turn_start(n)
        if note:
            self.notes.append(note[:600])
            del self.notes[:-NOTES_KEPT]
            self._turn_notes.setdefault(n, []).append(note[:600])
            self._turn_items.setdefault(n, []).append(await self.add(text_item("system", note)))
        if image is not None:
            iid = await self.add(image_item([image], detail=detail))
            self._images.append(iid)
            self._turn_items.setdefault(n, []).append(iid)
        return True

    async def _turn_start(self, n: int) -> None:
        """The first perception of turn n: the turn before's pictures go (its notes stay)."""
        if self._early_n == n:
            return
        self._early_n = n
        old, self._images = self._images, []
        for iid in old:
            await self.delete(iid)

    async def _perceived(self, n: int, seen: Perception) -> None:
        """What of this turn's perception did not go in during the hold goes in now: its text note (kept) and its
        pictures, each with its own detail (the window at low detail: its words are in the note)."""
        await self._turn_start(n)
        if seen.note:
            self.notes.append(seen.note[:600])
            del self.notes[:-NOTES_KEPT]
            self._turn_notes.setdefault(n, []).append(seen.note[:600])
            self._turn_items.setdefault(n, []).append(await self.add(text_item("system", seen.note)))
        for image in seen.images:
            iid = await self.add(image_item([image], detail=seen.details.get(image[1], "auto")))
            self._images.append(iid)
            self._turn_items.setdefault(n, []).append(iid)

    async def _forget_turn(self, n: int) -> None:
        """A cancelled press or a tap: what of it went in early is taken out again."""
        self._cancelled.add(n)
        for note in self._turn_notes.pop(n, []):
            if note in self.notes:
                self.notes.remove(note)
        for iid in self._turn_items.pop(n, []):
            await self.delete(iid)
            if iid in self._images:
                self._images.remove(iid)

    # --- input the VoiceSession interface asks for -------------------------------------------------
    async def send_audio(self, pcm: bytes) -> None:
        return None  # over WebRTC the browser sends the microphone to OpenAI itself

    async def send_text(self, text: str) -> None:
        """A typed line while talking: a user turn like speech."""
        if self._sb is None or self.closed or not text.strip():
            return
        for event in self.bus.withdraw("opener"):
            self._event_log(event, "the user spoke first")
        await self.add(text_item("user", text))
        self._last_user_at = self.clock()
        self.respond("typed", **self._deciding())

    async def inject(self, text: str, *, respond: bool = False, delegation_id: str | None = None,
                     source: str = "app", facts: dict | None = None) -> Event | None:
        """Context from the app (the narrator's lines, researchers' news, typed chat), on the bus. respond=True: said as
        one line in a pause (`proactive`); otherwise a silent system item, never answered (told once the connection is
        back, if it is away). Returns its event: the narrator marks what a line reports as said once it is heard
        (`Event.when_heard`), not when it starts."""
        if self.closed:
            return None
        if respond:
            tag = self.tag_notice(text) or {}
            event = (facts or {}).get("event") or tag.get("event") or (source if source != "app" else "notice")
            return await self.proactive(text, event=event, ref=(facts or {}).get("ref") or tag.get("ref"),
                                        before=tag.get("before"), mark=tag.get("mark"))
        kind = source if source in self.bus.kinds else "context"
        found, _ = self.bus.publish(kind, text, route="context", data={"source": source, "facts": dict(facts or {})})
        await self._tell_context()
        return found

    def idle_since(self) -> float:
        if self._held is not None or self.speaking():
            return 0.0
        last = max(self._last_audio_at, self._last_user_at)
        return self.clock() - last if last else float("inf")

    def last_speech_at(self) -> float:
        return self._last_audio_at

    def user_busy(self) -> bool:
        return self._held is not None or (self.clock() - self._last_user_at < IDLE_S if self._last_user_at else False)

    # --- what happens unasked: the bus's adapters (voice/events.py) ------------------------------------------------
    async def proactive(self, text: str, *, event: str, ref: str | None = None,
                        before: Callable[[], Awaitable[None]] | None = None, mark: str | None = None,
                        route: str | None = None, urgent: bool = False) -> Event:
        """Something that happened, on the bus: said in a pause, once per thing (its kind and reference, or its whole
        text), or told silently when its kind is context. `route` is the publisher's own (the watcher's rule) and
        `urgent` makes a failure interrupting; the bus's kinds decide otherwise. Never a prefix: lines of one template
        differ only in their facts."""
        found, how = self.bus.publish(event, text, ref=ref, route=route, urgent=urgent, mark=mark, before=before)
        if how == "duplicate":
            self._log({"kind": "partner-proactive", "event": event, "ref": ref, "eventT": self.wall(),
                       "userBusy": self.user_busy(), "skipped": "duplicate"})
            if found.route != "context" and text not in found.text and self._sb is not None:
                # The same event, told again with more facts (the announcer's line after the partner's own): known to
                # the voice, never said twice.
                found.text += "\n" + text
                with contextlib.suppress(Exception):
                    await self.add(text_item("system", "More about what was just said (context only):\n"
                                             + block(text, tag="hooke-event", source="app")))
            return found
        if how == "merged":
            self._event_log(found, "merged")
        if found.route == "context":
            await self._tell_context()
        else:
            self._consume()
        return found

    async def opener(self, *, note: str, images: list[tuple[bytes, str]]) -> Event | None:
        """Its first line as Talk first connects, once per Talk session (never on a reconnect): what it sees now goes
        into the conversation as a turn's perception does (the note kept, the pictures gone at the next turn), and one
        idle event goes on the bus, so it is said in a pause, never over the user, and let go if they speak first."""
        if self.closed or self._opened_line or self._sb is None:
            return None
        self._opened_line = True
        if note:
            self.notes.append(note[:600])
            del self.notes[:-NOTES_KEPT]
            await self.add(text_item("system", note))
        for image in images:
            self._images.append(await self.add(image_item([image], detail="auto")))
        found, _ = self.bus.publish("opener", note or "Talk connected", ref=f"open-{self.call_id or id(self)}")
        self._consume()
        return found

    async def deliver(self, task: str, *, facts: dict, say: str, points: list[str],
                      prepared: str | None = None, what: str = "think", kind: str | None = None,
                      receipt: str | None = None) -> Event | None:
        """A job's result (think, a slow tool), owed until heard: worded once by the voice itself, out of band, then said
        in a pause with marks on what it names as its words start (`_consume`). A second delivery of the same task adds
        nothing: it is said once. `kind`: job-result, job-failed or job-question (a job's question for the user); by
        default from the facts. `receipt`: how Hooke checked it (verified, failed, uncertain, running), which its words
        follow: done is said only for verified. Published even when this session has ended: the bus is the brain's, and
        the session saying its events now (its owner) says it."""
        if self.bus.result_for(task) is not None:
            return None
        if kind is None:
            kind = "job-failed" if isinstance(facts, dict) and facts.get("ok") is False else "job-result"
        found, _ = self.bus.publish(kind, json.dumps(facts, ensure_ascii=False, default=str)[:4000], ref=task,
                                    points=points, data={"task": task, "facts": facts, "say": say, "prepared": prepared,
                                                         "what": what, "receipt": receipt})
        owner = self.bus.owner
        if owner is not None and not getattr(owner, "closed", True) and hasattr(owner, "_consume"):
            owner._consume()
        return found

    async def tell_progress(self, task: str, working: Callable[[], bool], what: str = "think",
                            facts: Callable[[], str] | None = None, lines: int | None = None,
                            step: Callable[[], str] | None = None) -> None:
        """While a job works behind the voice: what is under way (what is running, how long so far), on the bus's
        progress channel once the voice has been quiet THINK_PROGRESS_AFTER_S since the job began, at most every
        THINK_PROGRESS_EVERY_S, THINK_PROGRESS_LINES in all, and a line after the first only when what it is doing
        (`step`) has changed, so each adds something. Said only in a pause and spaced (`_consume`), never owed,
        dropped once its result is out."""
        started = self.clock()
        most = THINK_PROGRESS_LINES if lines is None else lines
        last: float | None = None
        said_step: str | None = None  # what it was doing when its last line was said
        lines_seen = self.bus.progress_lines(task)
        published_step: str | None = None

        def current() -> bool:
            return working() and self.bus.result_for(task) is None

        def facts_now() -> str:
            return (facts() if facts is not None else self.running_facts()) or f"{what}, just started"

        while not self.closed and current() and self.bus.progress_lines(task) < most:
            await asyncio.sleep(0.25)
            if self.bus.progress_lines(task) > lines_seen:  # a line was said: what it said it was doing
                lines_seen, said_step = self.bus.progress_lines(task), published_step
            now = self.clock()
            quiet = min(self.idle_since(), now - started)  # quiet since its acknowledgement, at the least
            if (self.user_busy() or self.speaking() or self._asks or quiet < THINK_PROGRESS_AFTER_S
                    or self.bus.progress_waiting(task) or (last is not None and now - last < THINK_PROGRESS_EVERY_S)):
                continue
            doing = step() if step is not None else None
            if said_step is not None and doing == said_step:
                continue  # nothing new to say about it
            last, published_step = now, doing
            self.bus.publish("progress", facts_now(), ref=task, still=current, data={"facts": facts_now, "what": what})
            self._consume()

    # --- the bus's one consumer ------------------------------------------------------------------------------------
    def settled(self) -> bool:
        """Nobody talking, nothing said or asked for, and no tool of a turn still running or unanswered: a result
        from behind the voice may be said now (think-broad-2: said between a turn's answer and its tool round, the
        voice talked about that turn instead)."""
        return not (self.user_busy() or self.speaking() or self._asks or self._calls or self._round
                    or self.idle_since() < IDLE_S)

    def audible(self) -> bool:
        """The voice's words may still be playing: the output buffer has not stopped, or their length (from the
        response's audio tokens) has not run out."""
        return self._speaking or self._audio_until > self.clock()

    def _quiet(self) -> bool:
        """A pause: settled, and nothing still playing."""
        return self._sb is not None and self.settled() and not self.audible()

    def _turn_pending(self) -> bool:
        """The user's turn is still being answered: its tools running or unanswered, or its response asked for, under
        way or still playing. An interrupting event waits for it; it never cuts the user's own answer."""
        if self._calls or self._round or any(a.cause not in UNPROMPTED for a in self._asks):
            return True
        current = ((self._causes.get(self._response) or {}).get("cause") if self._response
                   else (self._requested or {}).get("cause"))
        if current is not None and current not in UNPROMPTED:
            return True
        return self.audible() and self._audible_cause not in UNPROMPTED

    def _consume(self) -> None:
        """Every WATCH_EVERY_S, and as an event is published. Silent notes are told at once. Then one response at a
        time: an interrupting event as soon as no key is held, the user's turn is answered and the line playing now has
        ended (never cut: a line the voice starts is finished, or yielded to the user); else, in a pause, what is due,
        results first, together in one response when they come within COALESCE_S of each other; else one progress
        line."""
        if self.closed or self.bus.owner is not self:
            return
        for event, why in self.bus.prune():
            self._event_log(event, why)
        if self._sb is not None and self.bus.context_due():
            self._spawn(self._tell_context())
        if self._saying is not None and not self._saying.done():
            return
        due = self.bus.due()
        urgent = [e for e in due if e.route == "interrupt"]
        if urgent and self._held is None and self._sb is not None and not self._turn_pending():
            if self.speaking() or self.audible():
                return  # the line playing now is finished first, never cut by our own next one (Will, 29 Oct)
            batch = (urgent + [e for e in due if e not in urgent])[:BATCH_MAX]
            self._saying = self._spawn(self._say(batch, urgent=True))
            return
        if not self._quiet():
            return
        if due:
            now = self.bus.clock()
            if now - max(e.since for e in due) < COALESCE_S and now - min(e.since for e in due) < COALESCE_MAX_S:
                return  # another may come within the window: said together
            openers = [e for e in due if e.kind == "opener"]
            if openers and len(openers) == len(due):
                self._ask_opener(openers[0])
                return
            for event in openers:  # something else is due: said instead, and the first line is let go
                self.bus.withdraw("opener")
                self._event_log(event, "something else was due")
            self._saying = self._spawn(self._say([e for e in due if e.kind != "opener"][:BATCH_MAX]))
            return
        progress = self.bus.progress_due()
        if progress is not None:
            self._ask_progress(progress)

    async def _tell_context(self) -> None:
        """Silent notes that are ready, each one system item, never answered (a burst of one kind is one item)."""
        if self.bus.owner is not self:
            return
        for event in self.bus.context_due():
            if self._sb is None:
                return  # told once the connection is back
            event.asking = True
            try:
                await self.add(text_item("system", "Context only, not a request; do not answer it:\n" + event.wrapped()))
            except Exception:
                event.asking = False
                return
            self.bus.told(event)
            if self.noted is not None:
                with contextlib.suppress(Exception):
                    self.noted({"kind": "voice-note", "source": event.data.get("source") or event.kind,
                                "sessionId": self.call_id, **(event.data.get("facts") or {}), "chars": len(event.text),
                                "text": event.text})

    async def _say(self, batch: list[Event], *, urgent: bool = False) -> None:
        """One response for what is due. A job's result is worded first, out of band and once (`prepare`); then all of
        it goes in one response, results first. Still due, as it was, if the user starts talking before it is sent."""
        self.bus.asked(batch)
        try:
            for event in batch:
                if event.data.get("task") and event.data.get("prepared") is None:
                    words = await self.prepare(event.data["task"], self._facts_text(event), event.data.get("say") or "",
                                               receipt=event.data.get("receipt"), kind=event.kind)
                    if words is None:  # the user's turn took its place: worded in the next pause
                        self.bus.released(batch)
                        return
                    event.data["prepared"] = words
            if urgent:
                if self._held is not None or self.speaking() or self.audible():
                    self.bus.released(batch)  # tried again as the line playing now ends
                    return
            elif not self._quiet():
                self.bus.released(batch)
                return
        except BaseException:
            self.bus.released(batch)
            raise
        self._ask_batch(batch, urgent=urgent)

    def _ask_batch(self, batch: list[Event], *, urgent: bool) -> None:
        """The response for them, queued at the gate: its own context (`input`: a result in the conversation's context
        was answered as the latest turn instead, think-check-2), its words still added to the conversation."""
        tasks = [e for e in batch if e.data.get("task")]
        again = any(e.cut is not None for e in batch)
        if len(batch) == 1 and tasks and not again:
            rule = "Say exactly the words you are given, once, as yourself, and nothing else."
        elif len(batch) == 1 and not again:
            rule = ("Now say exactly one short spoken sentence telling the user what just happened, from the facts you "
                    "are given: the outcome first, in plain words. No question, no offer, nothing else.")
        else:
            rule = ("Now tell the user what you are given, in one or two short spoken sentences, in the order given: "
                    "the results first. No question, no offer, nothing else.")
        if again:
            rule += (" You were cut off as you said some of it before: bring back, once and naturally (\"as I started "
                     "to say…\"), only what the user did not hear, without repeating what they did.")

        async def prepare() -> None:  # just before it is sent: its facts kept in the conversation, its marks drawn
            for e in batch:
                if not e.data.get("told"):
                    await self.add(text_item("system", self._told(e)))
                    e.data["told"] = True
                if e.before is not None:
                    with contextlib.suppress(Exception):
                        await e.before()

        def sent() -> None:
            self.bus.sent(batch)
            self.last_commentary_at = self.clock()
            points = [p for e in tasks for p in e.points][:4]
            if points:
                self._point = points
            for e in batch:
                if e.data.get("task"):
                    self._log({"kind": "partner-think-said", "task": e.data["task"], "n": self.n, "eventId": e.id,
                               "responseCreatedT": self.wall(), "pointAt": e.points, "again": e.interrupted})
                else:
                    self._log({"kind": "partner-proactive", "event": e.kind, "ref": e.ref, "eventT": e.wall,
                               "userBusy": False, "responseCreatedT": self.wall(), "eventId": e.id,
                               "again": e.interrupted, **({"markTarget": e.mark} if e.mark else {}),
                               **({"merged": e.merged} if e.merged else {})})

        def dropped(why: str) -> None:  # the user started talking first: due again in the next pause
            self.bus.released(batch)
            for e in batch:
                self._event_log(e, f"held: {why}")

        still = (lambda: self._held is None) if urgent else (lambda: not self.user_busy() and not self._speaking)
        metadata = {"events": ",".join(e.id for e in batch), **({"task": tasks[0].data["task"]} if tasks else {})}
        self.respond("think" if tasks else "proactive", first=urgent, still=still, prepare=prepare, sent=sent,
                     dropped=dropped, tool_choice="none", metadata=metadata,
                     input=[text_item("system", "\n\n".join(self._part(e) for e in batch))],
                     instructions=self.config.system_prompt + "\n\n" + rule)

    def _ask_opener(self, event: Event) -> None:
        """Its first line: in the conversation's own context (the brief, and the pictures just added), with mark as its
        only tool, so what it names is pointed at as it is said."""
        n = self.n
        marks = [d for d in self.tool_declarations() if d["name"] == "mark"]
        self.bus.asked([event])

        def sent() -> None:
            self.bus.sent([event])
            self._log({"kind": "partner-opener", "n": n, "eventId": event.id, "responseCreatedT": self.wall()})

        self.respond("opener", n=n, still=lambda: self._held is None and not self.user_busy() and self.n == n,
                     sent=sent, dropped=lambda why: self.bus.withdraw("opener"), metadata={"events": event.id},
                     tools=marks, tool_choice="auto" if marks else "none",
                     instructions=self.config.system_prompt + "\n\n" + OPENER_RULE)

    def _ask_progress(self, event: Event) -> None:
        """One progress line: in a pause, spaced per job (events.PROGRESS_SPACING_S), never owed."""
        task = event.ref or ""
        line = event.text
        with contextlib.suppress(Exception):
            facts = event.data.get("facts")
            line = (facts() if callable(facts) else "") or line  # as it is now, not as it was when published
        before = self.bus.progress_lines(task)
        again = " You already told them: say what has changed, in different words." if before else ""
        self.bus.asked([event])

        def sent() -> None:
            self.bus.sent([event])
            self._log({"kind": "partner-progress", "n": self.n, "task": task, "facts": line, "line": before + 1,
                       "eventId": event.id})

        self.respond("progress", still=lambda: event.holds() and self.bus.result_for(task) is None
                     and not self.user_busy(), sent=sent, dropped=lambda why: self.bus.released([event]),
                     tool_choice="none", max_output_tokens=PROGRESS_TOKENS, metadata={"task": task, "events": event.id},
                     instructions=self.config.system_prompt + "\n\nStill being worked on in Hooke, for what the user "
                     "asked: " + line + ". In one short sentence, under 12 words, tell the user what is under way "
                     "and how long it has taken so far. Do not say anything is done, found or shown." + again)

    @staticmethod
    def _facts_text(event: Event) -> str:
        return json.dumps(event.data.get("facts") or {}, ensure_ascii=False, default=str)[:4000]

    def _told(self, event: Event) -> str:
        """An event's facts as they are kept in the conversation, for the turns after it."""
        task = event.data.get("task")
        if task:
            return (f"Result of the task you started with {event.data.get('what') or 'think'} ({task}):\n"
                    + block(self._facts_text(event), tag="hooke-event", source="app"))
        return "Something happened (facts, not a request from the user):\n" + event.wrapped()

    @staticmethod
    def _part(event: Event) -> str:
        """An event as its response is given it: a result's own words, or its facts; and, if it was cut off before,
        what of it was heard (the voice judges what to bring back)."""
        words = event.data.get("prepared")
        if words:
            part = (f"The task you started with {event.data.get('what') or 'think'} has finished. Say this to the user "
                    "now, once, in your own voice:\n" + words)
        else:
            part = "Something happened (facts, not a request from the user):\n" + event.wrapped()
        cut = event.cut
        if cut is not None:
            heard, of = cut.get("heardS") or 0.0, cut.get("ofS")
            if heard <= 0:
                part += "\nYou began to say this before and were cut off before the user heard any of it."
            else:
                part += (f"\nYou began to say this before and were cut off about {heard:.1f} s in"
                         + (f" of {of:.1f} s" if of else "")
                         + (", while saying: " + inline(cut.get("words"), 600) if cut.get("words") else "") + ".")
        return part

    def _event_log(self, event: Event, why: str = "") -> None:
        self._log({"kind": "partner-event", "id": event.id, "event": event.kind, "ref": event.ref, "route": event.route,
                   "state": event.state, "interrupted": event.interrupted, **({"why": why} if why else {})})

    # --- heard, or cut off -----------------------------------------------------------------------------------------
    def _carry(self, rid: str, ids: str) -> None:
        """response.created: the events this response carries."""
        if self.bus.bind(rid, [i for i in ids.split(",") if i]):
            self._carried.setdefault(rid, {"started": None, "words": "", "audioS": None, "done": None})

    def _heard_to_end(self, rid: str) -> None:
        """output_audio_buffer.stopped: the buffer played out, this response's words to their end (and any carried
        before it that had started and was complete)."""
        ended = [rid] + [r for r, info in self._carried.items()
                         if r != rid and info["started"] is not None and info["done"] == "completed"]
        for r in ended:
            for e in self.bus.carried(r):
                self.bus.heard(e)
                self._event_log(e, "heard")
            self._carried.pop(r, None)

    def _cut_off(self, rid: str | None, why: str) -> None:
        """Cut before their words were heard to their end (one response's, or None: all playing). A result stays owed,
        once, with what of it was heard; anything else is dropped. Words whose whole length had played (from their
        audio tokens) were heard."""
        now = self.clock()
        for e in self.bus.carried(rid) if rid is not None else self.bus.playing():
            info = self._carried.get(e.rid or "") or {}
            started, length = info.get("started"), info.get("audioS")
            if started is not None and length and started + length <= now:
                self.bus.heard(e)
                self._event_log(e, "heard (played its whole length)")
                continue
            state = self.bus.cut(e, words=info.get("words") or "", of_s=length,
                                 heard_s=round(now - started, 2) if started is not None else 0.0)
            self._event_log(e, why)
            if state == "retired":
                self._retire(e)
        if rid is not None:
            self._carried.pop(rid, None)
        elif not self.bus.playing():
            self._carried.clear()

    def _retire(self, event: Event) -> None:
        """Cut off twice: never said a third time. Told to the voice as context, and handed to `on_retired`."""
        self._spawn(self._add_quietly(text_item("system", "Cut off twice before the user heard it, so not said again "
                                                          "(context only; tell them only if they ask):\n"
                                                + event.wrapped())))
        with contextlib.suppress(Exception):
            self.on_retired(event)

    async def _add_quietly(self, item: dict) -> None:
        with contextlib.suppress(Exception):
            await self.add(item)

    async def prepare(self, task: str, facts_text: str, say: str, *, receipt: str | None = None,
                      kind: str = "job-result") -> str | None:
        """The words for a job's result, written out of band by the voice itself (its conversation untouched), following
        its receipt (RECEIPT_RULES: done only when Hooke verified it) or, for a job's question, asking it; the receipt's
        own words (`say`) when that does not come back in PREP_WAIT_S. None when the user's turn took its place (a key
        press dropped or cancelled it): it is worded again in the next pause."""
        future = asyncio.get_running_loop().create_future()
        self._prepared[task] = future

        def dropped(why: str) -> None:
            if not future.done():
                future.set_result(None)
        if kind == "job-question":
            rule = ("Write the one short question you will ask the user for this task, from its question (a permission's "
                    "ask with what it costs). Nothing has been done about it yet: never say it is done.")
        elif kind == "job-cancelled":
            rule = ("This task was cancelled as the user asked, and has now stopped. Say that in a few words, with what "
                    "it had already done from its steps, and anything it was doing that may or may not have happened.")
        else:
            # One voice with every other line (PARTNER): the outcome first, plain words, and no shorter than an
            # explanation the user asked for, which lands whole.
            rule = ("Write what you will say to the user about this result, as yourself: the outcome first (what was "
                    "done or found, or what failed and the next thing to try), in one or two short spoken sentences; "
                    "when its own words are an explanation the user asked for, all of it, in plain spoken sentences. "
                    "Plain words: no ids, and how it was done only if the user asked how; nothing it did not check. Say "
                    "a refusal as a refusal.")
            if receipt in RECEIPT_RULES:
                rule += " " + RECEIPT_RULES[receipt]
        self.respond("prep", n=self.n, dropped=dropped, conversation="none", output_modalities=["text"],
                     tool_choice="none", max_output_tokens=PREP_TOKENS, metadata={"task": task},
                     input=[text_item("system", "Result of a task you handed on (facts, not the user's words):\n"
                                      + facts_text + (f"\nHooke checked it: its receipt is {receipt}." if receipt else "")
                                      + (f"\nIts own words: {say}" if say else ""))],
                     instructions=rule)
        try:
            text = await asyncio.wait_for(future, PREP_WAIT_S)
        except asyncio.TimeoutError:
            text = ""
        finally:
            self._prepared.pop(task, None)
        if text is None:
            return None
        # Never a claim of its own: the receipt's words, else a line that says only that there is news.
        fallback = ("That task is stopped." if kind == "job-cancelled" else "It's done." if receipt == "verified"
                    else "I have news on that task; ask me and I'll say what it found.")
        return (text or say or fallback).strip()

    async def _watch(self) -> None:
        """Every WATCH_EVERY_S: the bus consumed (`_consume`); a session near the limit renewed while idle (asked of
        the partner through a `go_away` event)."""
        asked_renewal = False
        while not self.closed:
            await asyncio.sleep(WATCH_EVERY_S)
            with contextlib.suppress(Exception):
                self._consume()
            if (not asked_renewal and self.clock() - self.opened_at > 55 * 60 and self.idle_since() > 5.0):
                asked_renewal = True
                self._emit(VoiceEvent("go_away", data={"reason": "limit", "time_left": "5m"}))
            if asked_renewal and self.clock() - self.opened_at < 60:
                asked_renewal = False  # renewed

    # --- receiving ---------------------------------------------------------------------------------
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

    async def _receive(self) -> None:
        sb = self._sb
        try:
            while True:
                ev = await sb.recv()
                await self.handle(ev)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.closed or self._reconnecting or sb is not self._sb:
                return
            # The connection went away under us: the partner reconnects (the sideband again, or a new call).
            self._connected.clear()
            self._emit(VoiceEvent("go_away", data={"reason": "close", "message": f"{type(exc).__name__}: {exc}"[:300]}))

    async def handle(self, ev: dict) -> None:
        """One server event, translated into VoiceEvents and the turn machine's state; the voice's phase is read again
        after it (a response created, its audio starting or stopping, a call, a response done)."""
        try:
            await self._handle(ev)
        finally:
            self._phase_changed()

    async def _handle(self, ev: dict) -> None:
        kind = ev.get("type")
        now = self.clock()
        if kind == "response.created":
            response = ev.get("response") or {}
            rid = response.get("id") or ""
            meta = response.get("metadata") or (self._requested or {})
            self._response, self._requested = rid, None
            self._causes[rid] = {"cause": meta.get("cause") or "unknown", "n": int(meta.get("n") or self.n),
                                 "createdT": self.wall(), "task": meta.get("task"), "events": meta.get("events"),
                                 "decide": meta.get("decide") == "1"}
            if meta.get("events"):
                self._carry(rid, str(meta["events"]))
            if self._unsay is not None and meta.get("cause") == "progress" and str(meta.get("n")) == str(self._unsay):
                self._unsay = None  # its round's result came while it was asked for: let go before it is heard
                self._spawn(self._cancel_unheard(rid))
        elif kind == "output_audio_buffer.started" or (kind == "response.output_audio.delta" and self.link != "webrtc"):
            # Over WebRTC the browser plays the audio: only the output buffer's events (and the studio's own
            # voice-phase) say when it is audible; a response's audio is generated faster than it plays.
            rid = ev.get("response_id") or self._response or ""
            if rid and rid not in self._first_audio:
                self._first_audio[rid] = self.wall()
                self._started_at[rid] = now
                if rid in self._out_audio:
                    self._audio_until = max(self._audio_until, now + self._out_audio.pop(rid) * AUDIO_TOKEN_S)
            if rid in self._carried and self._carried[rid]["started"] is None:
                self._carried[rid]["started"] = now  # playing: not yet heard (heard is the end, never the start)
            if rid:
                self._audible_cause = (self._causes.get(rid) or {}).get("cause")
            if not self._speaking:
                self._speaking = True
                self._emit(VoiceEvent("vad", data={"state": "model-start"}))
                self._point_now()
            self._last_audio_at = now
            if kind == "response.output_audio.delta":
                self._emit(VoiceEvent("audio", audio=base64.b64decode(ev.get("delta") or ""), rate=RATE))
        elif kind in ("output_audio_buffer.stopped", "output_audio_buffer.cleared") or (
                kind == "response.output_audio.done" and self.link != "webrtc"):
            if self._speaking:
                self._speaking = False
                self._last_audio_at = now
                self._emit(VoiceEvent("turn_complete", data={"reason": "quiet"}))
            if kind.startswith("output_audio_buffer."):
                self._audio_until = min(self._audio_until, now)  # when the browser stopped playing it (its response was logged)
                self._log({"kind": "partner-audio", "responseId": ev.get("response_id"), "audioEndT": self.wall(),
                           "how": kind.rsplit(".", 1)[-1]})
                if kind == "output_audio_buffer.stopped":
                    self._heard_to_end(ev.get("response_id") or "")  # the words it carried were heard to their end
                elif kind == "output_audio_buffer.cleared":
                    self._cut_off(ev.get("response_id") or None, "its audio was cleared")
        elif kind in ("response.output_audio_transcript.delta", "response.output_text.delta"):
            rid = ev.get("response_id") or self._response or ""
            delta = self._joined(rid, ev)
            if self._causes.get(rid, {}).get("cause") == "prep" or self._causes.get(rid, {}).get("decide"):
                self._transcripts[rid] = self._transcripts.get(rid, "") + delta
                return  # text the voice has not said (yet): never the chat's line
            self._transcripts[rid] = self._transcripts.get(rid, "") + delta
            self._emit(VoiceEvent("output_transcript", text=delta, data={"responseId": rid}))
        elif kind == "conversation.item.input_audio_transcription.delta":
            self._emit(VoiceEvent("input_transcript", text=ev.get("delta") or "",
                                  data={"interim": True, "delta": ev.get("delta") or "", "itemId": ev.get("item_id")}))
        elif kind == "conversation.item.input_audio_transcription.completed":
            text = (ev.get("transcript") or "").strip()
            if text:
                self._emit(VoiceEvent("input_transcript", text=text, data={"itemId": ev.get("item_id"), "n": self.n}))
        elif kind == "input_audio_buffer.speech_started":  # open mic only
            self._last_user_at = now
            self._emit(VoiceEvent("vad", data={"state": "start"}))
            if self.talk == "open":
                self.n += 1
                self.on_speech_started(self.n)
        elif kind == "input_audio_buffer.speech_stopped":
            self._last_user_at = now
            self._emit(VoiceEvent("vad", data={"state": "end"}))
        elif kind == "input_audio_buffer.committed" and self.talk == "open":
            # The server ended the user's turn: what was seen goes in, then the answer is asked for (in a task: this
            # receiver must keep reading the events that answer depends on).
            self._spawn(self._open_turn(self.n))
        elif kind == "response.function_call_arguments.done":
            self._call(ev.get("response_id") or self._response or "", ev.get("call_id") or "", ev.get("name") or "",
                       ev.get("arguments"))
        elif kind == "response.output_item.done":
            item = ev.get("item") or {}
            if item.get("type") == "function_call":
                self._call(ev.get("response_id") or self._response or "", item.get("call_id") or "",
                           item.get("name") or "", item.get("arguments"), item.get("status"))
        elif kind == "response.done":
            await self._response_done(ev.get("response") or {})
        elif kind == "error":
            self._provider_error(ev.get("error") or {})
        elif kind == "session.created":
            session = ev.get("session") or {}
            self.session_id = session.get("id") or self.session_id

    @staticmethod
    def _said_in(response: dict) -> str:
        """A response's words from its output items, each part joined to the one before by a space."""
        parts = [str(part.get("transcript") or part.get("text") or "").strip()
                 for item in response.get("output") or [] if item.get("type") == "message"
                 for part in item.get("content") or []]
        return " ".join(p for p in parts if p)

    def _joined(self, rid: str, ev: dict) -> str:
        """A transcript delta as the response's words go on: the first delta of a new part (another message or content
        part of the same response, as around a tool call) starts with a space when the words before it end without one
        (Will's feel check, 29 Oct: "…easy to spot.That central oval…")."""
        delta = ev.get("delta") or ""
        part = (ev.get("item_id"), ev.get("content_index"))
        last = self._parts.get(rid)
        self._parts[rid] = part
        before = self._transcripts.get(rid, "")
        if last is not None and part != last and before and delta and not before[-1].isspace() \
                and not delta[0].isspace():
            return " " + delta
        return delta

    def _provider_error(self, error: dict) -> None:
        """A provider error is logged, never an error that ends Talk: each one the API sends here is about one request
        of ours (a create refused while another runs, a cancel or clear with nothing to stop, an item already gone, an
        empty commit). A refused response.create is asked again, once the response in progress has ended; a lost
        connection is handled where the connection ends (reconnect)."""
        code, event_id = error.get("code"), str(error.get("event_id") or "")
        self._log({"kind": "partner-provider-note" if event_id.startswith("be_") else "partner-provider-error",
                   "code": code, "eventId": event_id or None, "message": str(error.get("message") or "")[:300]})
        requested = self._requested
        if requested is not None and event_id and event_id == requested.get("eventId"):
            self._requested = None
            ask = requested.get("ask")
            if code == "conversation_already_has_active_response" and ask is not None and ask.tries < 2:
                ask.tries += 1
                self._response = self._response or "unknown"  # one we did not ask for is running: wait for its end
                self._asks.insert(0, ask)
            self._gate_wake.set()

    async def _open_turn(self, n: int) -> None:
        seen = self.turn_items(n)
        committed = self.wall()
        await self._perceived(n, seen)
        self._rounds_this_turn = 0
        self.respond("turn", n=n, sent=lambda: self._log({
            "kind": "partner-turn", "n": n, "how": "open", "releasedT": None, "committedT": committed,
            "responseCreatedT": self.wall(), **seen.facts}), **self._deciding())

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._late.add(task)
        task.add_done_callback(self._late.discard)
        return task

    async def _response_done(self, response: dict) -> None:
        rid = response.get("id") or self._response or ""
        self._last_done_at = self.clock()
        if rid == self._response or self._response == "unknown":
            self._response = None
        if self._requested is not None and not self._causes.get(rid):
            self._requested = None
        self._gate_wake.set()
        cause = self._causes.pop(rid, {"cause": "unknown", "n": self.n, "createdT": None})
        facts = usage_facts(response.get("usage"))
        self._parts.pop(rid, None)
        if cause.get("cause") == "prep":
            words = self._transcripts.pop(rid, "") or self._said_in(response)
            meta = response.get("metadata") or {}
            future = self._prepared.get(meta.get("task") or cause.get("task") or "")
            if future is not None and not future.done():
                # Cancelled (a key press): worded again in the next pause, not said in the backend's words.
                future.set_result(None if response.get("status") == "cancelled" else words)
            self._log({"kind": "partner-response", "responseId": rid, "n": cause["n"], "cause": "prep",
                       "task": meta.get("task") or cause.get("task"), "status": response.get("status"),
                       "text": words, "usage": facts})
            self._emit(VoiceEvent("usage", data={"seconds": None, "tokens": facts,
                                                 "usd": usage_usd(facts, self.config.model), "responseId": rid,
                                                 "cause": "prep"}))
            return
        if facts["outAudio"]:  # how long its words play: from their start, or from now when they have not started
            start = self._started_at.pop(rid, None)
            if start is not None:
                self._audio_until = max(self._audio_until, start + facts["outAudio"] * AUDIO_TOKEN_S)
            else:
                self._out_audio[rid] = facts["outAudio"]
        status = response.get("status")
        transcript = self._transcripts.pop(rid, "") or self._said_in(response)
        decided = bool(cause.get("decide"))
        if decided:  # a turn answered in text first: none of its words were said by it (`_decided`)
            written, transcript = transcript, ""
        self._emit(VoiceEvent("usage", data={"seconds": None, "tokens": facts, "usd": usage_usd(facts, self.config.model),
                                             "responseId": rid, "cause": cause["cause"]}))
        self._emit(VoiceEvent("generation_complete", data={"responseId": rid, "status": status}))
        first = self._first_audio.pop(rid, None)
        carried = self._carried.get(rid)
        if carried is not None:  # a response saying the bus's events: heard only when its audio stops (`_heard_to_end`)
            carried["words"], carried["done"] = transcript, status
            if facts["outAudio"]:
                carried["audioS"] = facts["outAudio"] * AUDIO_TOKEN_S
            if status in ("cancelled", "incomplete", "failed"):
                self._cut_off(rid, f"its response was {status}")
            elif response.get("usage") and not facts["outAudio"]:
                self._cut_off(rid, "its response made no audio")
        self._log({"kind": "partner-response", "responseId": rid, "n": cause["n"], "cause": cause["cause"],
                   "firstAudioT": first,
                   "audioEndT": self.wall() if self.link != "webrtc" else None,  # WebRTC: partner-audio says it
                   "cancelled": status == "cancelled", "status": status, "transcript": transcript, "usage": facts,
                   # the bus's events it said (the bench counts an outcome brought back after a cut once)
                   **({"events": [i for i in str(cause["events"]).split(",") if i]} if cause.get("events") else {}),
                   **({"silent": True, "written": written} if decided else {})})
        for item in response.get("output") or []:
            if item.get("type") == "function_call":
                self._call(rid, item.get("call_id") or "", item.get("name") or "", item.get("arguments"),
                           item.get("status"))
        says = await self._decided(rid, cause, response, written) if decided else False
        if self._round.get(rid):
            # Answered even when the response was cancelled (the user spoke): their next answer reads the outputs.
            # Only marks, after the answer was spoken: pointing is done, and nothing more is said about it.
            names = {self._call_names.get(cid) for cid in self._round[rid]}
            spoke = bool(transcript.strip())
            # think and mark only, after speaking: the acknowledgement was said; think's result follows on its own.
            # think without a word said: one short acknowledgement, never two (HeyClicky's duplicate one).
            # Pointing goes with the words (HeyClicky's point_at_element sits alongside speech): marks after words get
            # no response of their own unless one was not shown (Will's feel check, 29 Oct: "It's highlighted around
            # the central oval…" said again after the reply had said it). Marks with no words before them go on to the
            # answer.
            if decided:
                spoke = says  # its words are said now, with its marks (`_decided`); before any other tool, nothing was
            quiet_round = names <= {"mark"} | self.started_tools
            ack = bool(names & self.started_tools) and quiet_round and not spoke
            pointing = names == {"mark"} and spoke
            go_on = (not (quiet_round and spoke) or pointing) and not ack
            # Whether the voice said words before its tools (its lead-in, "Outlining that now."): from the response's
            # own words (its transcript, its audio), since its first-audio mark is popped above and over WebRTC its
            # audio may not have started yet (Will's try-out, 29 Oct: the lead-in was never seen, so "still running" was said after it).
            led_in = not decided and (spoke or first is not None or bool(facts.get("outAudio")))
            self._spawn(self._finish_round(rid, cause["n"], go_on=go_on, ack=ack, pointing=pointing, led_in=led_in))

    async def _decided(self, rid: str, cause: dict, response: dict, written: str) -> bool:
        """A turn answered in text first (option PARTNER_SPEAKS "after-deciding"): its words were decided, not said.
        With a tool other than mark called, they are let go unsaid (a lead-in before work is a guess at what Hooke will
        do), and the tool's outcome is said after it as before. With none, or only marks, they are its answer: said now,
        exactly, by one audio response (`_say_decided`), its marks drawn with them. Either way the written message is
        taken out of the conversation, so the conversation holds one assistant message for the answer (the audio one),
        never a text copy beside it, and nothing that was not said. Whether its words are being said."""
        names = {item.get("name") for item in response.get("output") or [] if item.get("type") == "function_call"}
        names |= {self._call_names.get(cid) for cid in self._round.get(rid, [])}  # each call as its arguments ended
        names.discard(None)
        words = spoken(written)
        says = response.get("status") == "completed" and bool(words) and names <= {"mark"}
        if says:  # asked for first: its own context is its words, so nothing it says waits on the deletes below
            self._say_decided(cause["n"], words, rid)
        for item in response.get("output") or []:
            if item.get("type") == "message" and item.get("id"):
                await self.delete(item["id"])
        self._log({"kind": "partner-decided", "n": cause["n"], "responseId": rid, "createdT": cause.get("createdT"),
                   "doneT": self.wall(), "status": response.get("status"), "calls": sorted(n for n in names if n),
                   "how": "said" if says else "tools" if names else "nothing", "words": written[:600]})
        return says

    def _say_decided(self, n: int, words: str, decided: str) -> None:
        """A decided answer said: one audio response whose only context is those words, said as they are; its audio is
        the answer's one message in the conversation (a barge-in truncates it, as any answer's)."""
        self.respond("say", n=n, tool_choice="none", metadata={"decided": decided},
                     input=[text_item("system", "Say these words to the user now, exactly as written:\n" + words)],
                     instructions=SAY_DECIDED)

    # --- tools ---------------------------------------------------------------------------------------
    def _call(self, rid: str, call_id: str, name: str, arguments, status: str | None = None) -> None:
        """A function call the model completed runs; one cut off (a cancelled response's incomplete call, arguments
        that don't parse) is dropped and logged, never run with what it lacked."""
        if not call_id or call_id in self._seen_calls:
            return
        self._seen_calls.add(call_id)
        try:
            args = json.loads(arguments) if isinstance(arguments, str) and arguments.strip() else (
                {} if arguments in (None, "") and status in (None, "completed") else None)
        except ValueError:
            args = None
        if status not in (None, "completed") or not isinstance(args, dict):
            self._log({"kind": "partner-call-dropped", "callId": call_id, "name": name, "status": status,
                       "why": "not completed" if status not in (None, "completed") else "its arguments did not parse"})
            return
        self._round.setdefault(rid, []).append(call_id)
        self._call_epoch[call_id] = self._epoch
        self._call_names[call_id] = name
        event = VoiceEvent("tool_call", call_id=call_id, name=name, args=args)
        self._emit(event)
        self._calls[call_id] = asyncio.ensure_future(self._run_tool(event))
        self._phase_changed()

    async def _run_tool(self, call: VoiceEvent) -> dict:
        spec = next((t for t in self.config.tools if t.name == call.name), None)
        started, t0 = self.wall(), time.perf_counter()
        try:
            result = await spec.handler({**call.args, "_call_id": call.call_id}) if spec is not None else \
                {"ok": False, "error": f"there is no {call.name} tool"}
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the model is told plainly; the real error is in the log
            result = {"ok": False, "error": "something went wrong in the app",
                      "_log": {"error": f"{type(exc).__name__}: {exc}"[:300]}}
        result = result if isinstance(result, dict) else {"result": result}
        ms = round((time.perf_counter() - t0) * 1000)
        log = result.pop("_log", {}) if isinstance(result.get("_log"), dict) else {}
        self._log({"kind": "partner-tool", "n": self.n, "callId": call.call_id, "name": call.name,
                   "args": json.dumps(call.args, ensure_ascii=False)[:500], "startT": started, "endT": self.wall(),
                   "ok": result.get("ok", "error" not in result), **({"error": result["error"]} if result.get("error") else {}),
                   "screenChange": False, "reportT": None, "confirmed": None, **log})
        self._emit(VoiceEvent("tool_result", call_id=call.call_id, name=call.name,
                              result={k: v for k, v in result.items() if k != "_look"}, data={"ms": ms}))
        self._phase_changed()
        return result

    async def _finish_round(self, rid: str, n: int, go_on: bool = True, ack: bool = False,
                            pointing: bool = False, led_in: bool = False) -> None:
        """A tool round being answered is the voice thinking (`phase`) from its first call to the response it asks for
        (or none): counted open while `_answer_round` runs."""
        self._rounds_open += 1
        try:
            await self._answer_round(rid, n, go_on=go_on, ack=ack, pointing=pointing, led_in=led_in)
        finally:
            self._rounds_open -= 1
            self._phase_changed()

    async def _answer_round(self, rid: str, n: int, go_on: bool = True, ack: bool = False,
                            pointing: bool = False, led_in: bool = False) -> None:
        """Every call of one response answered (as each is ready), then the model asked to go on, through the gate:
        dropped there if the user has started a newer turn (that turn's answer reads these outputs) or the call has
        been replaced (a new call never had these call ids: nothing is posted to it). `pointing`: marks made beside the
        reply's words, which go on only if one was not shown (the Realtime API leaves the next response.create to the
        client)."""
        ids = self._round.pop(rid, [])
        epoch = next((self._call_epoch[cid] for cid in ids if cid in self._call_epoch), self._epoch)
        tasks = {cid: self._calls[cid] for cid in ids if cid in self._calls}
        progress: list[Ask] = []
        began = self.clock()
        pending = set(tasks.values())
        looked = False
        still_running = True  # every output so far says its tool is still running
        let_go = False  # its progress line, not yet heard, was let go once its work landed
        shown = True  # every mark of a pointing round was drawn
        sent: set[str] = set()
        while pending:
            done, pending = await asyncio.wait(pending, timeout=0.1)
            for cid, task in tasks.items():
                if task in done and cid not in sent:
                    sent.add(cid)
                    if epoch != self._epoch:
                        self._log({"kind": "partner-output-dropped", "callId": cid, "why": "its call was replaced"})
                        continue
                    still_running &= self._still_running(task)
                    shown &= self._all_shown(task)
                    looked |= await self._output(cid, task)
            # One line, once the voice has said nothing for PROGRESS_AFTER_S while the tool runs: only a silence that
            # would feel broken is filled.
            now = self.clock()
            quiet_since = max(began, self._last_audio_at, self._audio_until, self._last_user_at)
            handed = bool(ids) and all(self._call_names.get(cid) == "do_in_hooke" for cid in ids)
            if handed and not led_in:
                # Nothing said of it yet: one line on what Hooke is doing now, once it is long work (`_handoff_due`).
                facts = self._handoff_due(ids, now - quiet_since) if pending and not progress else None
                due, head, words = facts is not None, "Being done in Hooke now, for what the user just asked: ", \
                    HANDOFF_PROGRESS_WORDS
            else:
                # Its lead-in said it is under way: a line only if it runs long.
                wait = PROGRESS_AFTER_LEADIN_S if led_in else PROGRESS_AFTER_S
                due, facts = now - quiet_since >= wait, None
                head, words = "Still running in Hooke for what the user asked: ", PROGRESS_WORDS
            if progress and not let_go and handed and not led_in and not self._handoff_working(ids):
                # Its work landed while its line was on its way: a line not yet heard is let go (the outcome follows).
                let_go = True
                self._unsay_progress(n)
            if (pending and len(progress) < PROGRESS_LINES and due
                    and not self.audible() and self._requested is None and self._response is None and not self._asks
                    and self._held is None and n == self.n and epoch == self._epoch):
                facts = facts or self.running_facts() or "the tool you called"
                self._log({"kind": "partner-progress", "n": n, "facts": facts, "line": len(progress) + 1})
                working = (lambda ids=ids: self._handoff_working(ids)) if handed and not led_in else (lambda: True)
                progress.append(self.respond(
                    "progress", n=n, still=lambda pending=pending, working=working: (
                        any(not t.done() for t in pending) and working()),
                    tool_choice="none", max_output_tokens=PROGRESS_TOKENS,
                    instructions=self.config.system_prompt + "\n\n" + head + facts + ". " + words))
        if progress:
            self._unsay_progress(n)  # its result is here: a line not yet heard is let go, and the result said instead
        for cid in ids:
            self._calls.pop(cid, None)
        if self.closed or epoch != self._epoch:
            return
        # A hand-off's row shows it working (handoffs.py), as a lead-in would have said: its still-running output is not
        # said either (will-workspace, 29 Oct: "It's being created now. I'll only confirm once Hooke says…").
        handed = bool(ids) and all(self._call_names.get(cid) == "do_in_hooke" for cid in ids)
        if (progress or led_in or handed) and still_running and go_on and not ack:
            # A progress line, or the voice's own words before the tool in the reply that called it ("Outlining that
            # now."), said it is under way; outputs that only say so again are not said a second time (the outcome
            # comes as one line when it lands). Will's try-out, 29 Oct: three "still working" lines for one request.
            self._log({"kind": "partner-round-quiet", "n": n,
                       "why": "a progress line said it is under way" if progress else
                       "its lead-in said it is under way" if led_in else "its row shows it working"})
            return
        if pointing and shown:
            self._log({"kind": "partner-round-quiet", "n": n, "why": "the marks went with the words"})
            return
        if ack:
            self.respond("ack", n=n, tool_choice="none", max_output_tokens=ACK_TOKENS,
                         instructions=self.config.system_prompt + "\n\nSay only a two- or three-word acknowledgement "
                         "that you are on it (\"On it.\"), nothing else.")
            return
        if not go_on:
            return
        self._rounds_this_turn += 1
        if self._rounds_this_turn > TOOL_ROUNDS:
            self._log({"kind": "partner-rounds-stopped", "n": n, "rounds": self._rounds_this_turn})
            self.respond("tool", n=n, tool_choice="none")
            return
        self.respond("look" if looked else "tool", n=n)

    def _handoff_due(self, ids: list[str], quiet: float) -> str | None:
        """The facts for a hand-off's one progress line when it is due (HANDOFF_PROGRESS_AFTER_S quiet and one of its
        steps running HANDOFF_STEP_S, or HANDOFF_THINKING_S quiet with none), from what its brain is doing right now;
        None while it is not (quick work, or nothing known of it)."""
        if quiet < HANDOFF_PROGRESS_AFTER_S:
            return None
        doing = []
        for cid in ids:
            with contextlib.suppress(Exception):
                found = self.handoff_doing(cid)
                if isinstance(found, dict) and found.get("facts"):
                    doing.append(found)
        if not doing or not (quiet >= HANDOFF_THINKING_S or any(d.get("stepS", 0) >= HANDOFF_STEP_S for d in doing)):
            return None
        return "; ".join(str(d["facts"]) for d in doing)

    def _handoff_working(self, ids: list[str]) -> bool:
        """Whether Hooke is still doing a hand-off's work (a step running, or still deciding), not only writing its
        answer once its steps are done (partner_tools `handoff_doing`'s `working`)."""
        for cid in ids:
            with contextlib.suppress(Exception):
                found = self.handoff_doing(cid)
                if isinstance(found, dict) and found.get("working", True):
                    return True
        return False

    @staticmethod
    def _still_running(task: asyncio.Task) -> bool:
        """Whether a call's output only says its tool is still running (answered "still working" after TOOL_WAIT_S)."""
        try:
            result = task.result()
        except BaseException:  # noqa: BLE001 - a broken handler is an outcome to say
            return False
        return isinstance(result, dict) and bool(result.get("still_working")) and result.get("result") in (None, "running")

    @staticmethod
    def _all_shown(task: asyncio.Task) -> bool:
        """Whether a mark call's output says every mark it asked for was drawn (read only for a round of marks)."""
        try:
            result = task.result()
        except BaseException:  # noqa: BLE001 - a broken handler drew nothing
            return False
        return (isinstance(result, dict) and result.get("ok") is not False and bool(result.get("shown"))
                and not result.get("not_shown"))

    def _unsay_progress(self, n: int) -> None:
        """The round's result is here while its progress line may still be on its way. A line not yet heard starting is
        let go: one still queued drops itself (its `still` is now false), one asked for or being made is cancelled
        before its first audio. The result is said instead. A line already playing is heard to its end, never cut."""
        rid = self._response
        cause = self._causes.get(rid or "") if rid else None
        if rid and cause and cause.get("cause") == "progress" and cause.get("n") == n:
            self._spawn(self._cancel_unheard(rid))
        elif self._requested is not None and self._requested.get("cause") == "progress":
            self._unsay = n  # cancelled as it is created (response.created)

    async def _cancel_unheard(self, rid: str) -> None:
        if rid in self._first_audio or self._response != rid:
            return  # already heard starting: it plays to its end, and the result follows it
        self._log({"kind": "partner-progress-unsaid", "responseId": rid, "n": self.n})
        with contextlib.suppress(Exception):
            await self._send({"type": "response.cancel", "response_id": rid, "event_id": best_effort()})
        with contextlib.suppress(Exception):
            await self._send({"type": "output_audio_buffer.clear", "event_id": best_effort()})

    async def _output(self, call_id: str, task: asyncio.Task) -> bool:
        """One call's output into the conversation; a fresh look after a change on screen goes in beside it."""
        try:
            result = task.result()
        except BaseException as exc:  # noqa: BLE001 - a cancelled or broken handler is said as such
            result = {"ok": False, "error": f"the tool did not finish ({type(exc).__name__})"}
        look = result.pop("_look", None)
        points = result.pop("_point", None)
        if isinstance(points, list) and points:
            # Ids, or a frame mark ({target: "frame", region, frame}): what the answer about this names, pointed at as
            # it starts to be heard.
            self._point = [t if isinstance(t, dict) else str(t) for t in points][:4]
        # The receipt first (its result and its words): what the voice says comes from them; the rest is detail for
        # when the user asks how (smoke-ux-2 still said "the outlines are now on the layer" from the tool's summary).
        first = {k: result[k] for k in ("result", "said") if k in result}
        output = json.dumps({**first, **{k: v for k, v in result.items() if k not in first}}, ensure_ascii=False,
                            default=str)[:12000]
        await self.add({"id": item_id("fo"), "type": "function_call_output", "call_id": call_id, "output": output})
        if isinstance(look, dict) and (look.get("images") or look.get("text")):
            note = str(look.get("text") or "")
            if note:
                await self.add(text_item("system", note))
            if look.get("images"):
                self._images.append(await self.add(image_item(look["images"], detail=look.get("detail", "low"))))
            return True
        return False


# --- one real session without a browser (the paid smoke test and the app's own model check) -------------------------
PROBE_TOOL = {"type": "function", "name": "count_cells",
              "description": "Count the cells in the camera picture with the app's counter. Always use it for a count.",
              "parameters": {"type": "object", "properties": {"what": {"type": "string"}}, "required": ["what"]}}


async def probe(model: str = MODEL, *, image: bytes | None = None, api_key: str | None = None, open_sideband=None,
                timeout: float = 30.0) -> dict:
    """One session over the WebSocket alone: the config with a tool and no turn detection, a user item with a picture and
    words, the tool's call answered with function_call_output, then a spoken answer. Returns what happened: the event
    types in order, the call, the answer's transcript, the usage and the times from each response.create to its first
    audio delta (docs/architecture/seeing-and-acting.md, "The one-brain loop")."""
    if open_sideband is None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"], max_retries=0)
        sb = await SdkSideband(client.realtime.connect(model=model)).open()
    else:
        sb = await open_sideband(model)
    seen: list[str] = []
    out: dict[str, Any] = {"events": seen, "firstAudioMs": [], "usage": [], "transcript": "", "call": None,
                           "errors": []}
    session = {"type": "realtime", "model": model, "output_modalities": ["audio"],
               "instructions": "You are a microscope assistant. Answer in one short spoken sentence. Use count_cells "
                               "for any count.",
               "audio": {"input": {"turn_detection": None, "noise_reduction": {"type": "far_field"},
                                   "transcription": {"model": TRANSCRIBE_MODEL}}},
               "tools": [PROBE_TOOL], "tool_choice": "auto", "reasoning": {"effort": "low"},
               "truncation": {"type": "retention_ratio", "retention_ratio": 0.8}}
    content: list[dict] = [{"type": "input_text", "text": "How many cells are in this picture? Count them."}]
    if image is not None:
        content.insert(0, {"type": "input_image", "detail": "low",
                           "image_url": "data:image/jpeg;base64," + base64.b64encode(image).decode()})

    async def until(kinds: set[str]) -> dict:
        while True:
            ev = await asyncio.wait_for(sb.recv(), timeout)
            kind = ev.get("type", "")
            if not seen or seen[-1] != kind:
                seen.append(kind)
            if kind == "error":
                out["errors"].append(ev.get("error"))
            if kind == "response.output_audio.delta" and out.get("_asked") is not None:
                out["firstAudioMs"].append(round((time.perf_counter() - out.pop("_asked")) * 1000))
            if kind in ("response.output_audio_transcript.delta",):
                out["transcript"] += ev.get("delta") or ""
            if kind in kinds:
                return ev

    try:
        await until({"session.created"})
        await sb.send({"type": "session.update", "session": session})
        await until({"session.updated"})
        await sb.send({"type": "conversation.item.create",
                       "item": {"type": "message", "role": "user", "content": content}})
        out["_asked"] = time.perf_counter()
        await sb.send({"type": "response.create"})
        first = await until({"response.done"})
        response = first.get("response") or {}
        out["usage"].append(usage_facts(response.get("usage")))
        call = next((o for o in response.get("output") or [] if o.get("type") == "function_call"), None)
        if call is None:
            raise RuntimeError(f"no tool call (status {response.get('status')}: {response.get('status_details')})")
        out["call"] = {"name": call.get("name"), "arguments": call.get("arguments")}
        await sb.send({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": call["call_id"],
            "output": json.dumps({"ok": True, "count": 7, "summary": "7 cells counted"})}})
        out["_asked"] = time.perf_counter()
        await sb.send({"type": "response.create"})
        second = await until({"response.done"})
        out["usage"].append(usage_facts((second.get("response") or {}).get("usage")))
        out["status"] = (second.get("response") or {}).get("status")
    finally:
        out.pop("_asked", None)
        with contextlib.suppress(Exception):
            await sb.close()
    out["usd"] = round(sum(usage_usd(u, model) for u in out["usage"]), 5)
    return out
