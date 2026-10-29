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
  - lines nobody asked for (a researcher finished, a long tool done, a capability turned on, a reconnect) are said as
    one sentence only when nobody is talking (`proactive`), at most once per event;
  - the 60-minute limit or a lost connection: `reconnect` makes a new call (the browser's new offer) or reattaches the
    sideband, seeded with the recent conversation, the brief and the per-turn notes, as the same session object.

Event names and shapes as they worked against the real API on 2026-10-29 are in docs/bench/realtime-partner.md.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

from .base import VoiceConfig, VoiceEvent, VoiceSession

MODEL = "gpt-realtime-2.1"  # option VOICE's one-brain choice (catalog/models/gpt-realtime-2-1)
RATE = 24000
# The user's words for the chat and the tools' checks of what was asked (input audio transcription; a fixed role).
TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
# WebRTC audio is still in flight when the key is released: the commit waits this long so the last word is in the
# buffer (the studio keeps the track on a little longer). Measured on the journey bench, not guessed beyond this.
RELEASE_TAIL_S = 0.15
MIN_HOLD_S = 0.25  # a press shorter than this is a tap: nothing to commit, so no answer
IDLE_S = 1.5  # nobody has spoken for this long: a line nobody asked for may be said
PROACTIVE_KEPT_S = 60.0  # a waiting proactive line is dropped (only noted) after this long
SAME_EVENT_S = 120.0  # the same event (kind and reference) is said at most once in this long
RESPONSE_WAIT_S = 3.0  # a new response waits at most this long for the one in progress to end
PROGRESS_AFTER_S = 2.5  # tools still running this long after the model went quiet: one progress line
TOOL_ROUNDS = 8  # tool rounds one user turn may chain (a model calling tools forever is stopped)
NOTES_KEPT = 12  # per-turn text notes kept for a reconnect's seed
CLOSE_STEP_S = 2.0


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


@dataclass
class Waiting:
    """A line nobody asked for, waiting for quiet."""
    text: str
    event: str
    ref: str | None = None
    at: float = field(default_factory=time.monotonic)
    before: Callable[[], Awaitable[None]] | None = None  # e.g. the mark on the card, drawn as it is said
    mark: str | None = None


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


class RealtimeSession(VoiceSession):
    def __init__(self, config: VoiceConfig, *, api_key: str | None = None, talk: str = "hold",
                 create_call: Callable[[str, dict], Awaitable[tuple[str, str]]] | None = None,
                 open_sideband: Callable[[str], Awaitable[Any]] | None = None,
                 hangup: Callable[[str], Awaitable[None]] | None = None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        """`create_call(offer_sdp, session) -> (answer_sdp, call_id)`, `open_sideband(call_id) -> sideband` (send(dict),
        recv() -> dict, close()), `hangup(call_id)`: the SDK's by default; tests pass fakes."""
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
        # What a line the app asks to be said is about, when the partner knows (a researcher that just finished):
        # {"event", "ref", "mark", "before"} or None.
        self.tag_notice: Callable[[], dict | None] = lambda: None
        # --- conversation state ---
        self.n = 0  # the user's turn number (talk presses, or speech in open mic)
        self._held: int | None = None
        self._held_at = 0.0
        self._released_at = 0.0
        self._requested: dict | None = None  # a response.create sent, not yet created
        self._response: str | None = None  # the response in progress
        self._causes: dict[str, dict] = {}  # response id -> {"cause", "n", "createdT"}
        self._transcripts: dict[str, str] = {}  # response id -> the assistant's words so far
        self._speaking = False
        self._last_audio_at = 0.0
        self._last_user_at = 0.0
        self._first_audio: dict[str, float] = {}
        self._calls: dict[str, asyncio.Task] = {}  # call id -> its handler's task
        self._seen_calls: set[str] = set()
        self._round: dict[str, list[str]] = {}  # response id -> its calls
        self._rounds_this_turn = 0
        self._images: list[str] = []  # image items of the last turn: deleted at the next
        self._brief: tuple[str, str] | None = None  # (item id, revision)
        self.brief_text = ""
        self.notes: list[str] = []  # each turn's short text note of what was seen
        self._waiting: list[Waiting] = []
        self._said_events: dict[tuple, float] = {}
        self._progress_said: set[str] = set()
        self._reconnecting = False
        self.reconnects = 0
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
            "reasoning": {"effort": extra.get("reasoning", "low")},
            "truncation": {"type": "retention_ratio", "retention_ratio": 0.8},
        }
        if self.config.system_prompt:
            session["instructions"] = self.config.system_prompt
        tools = self.tool_declarations()
        if tools:
            session["tools"], session["tool_choice"] = tools, "auto"
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
        self._emit(VoiceEvent("ready", data={"session_id": self.call_id, "link": "webrtc"}))

    async def _open_call(self, offer_sdp: str) -> None:
        self.answer_sdp, self.call_id = await self._create_call(offer_sdp, self.session_config())
        self.session_id = self.call_id
        try:
            self._sb = await self._open_sideband(self.call_id)
        except BaseException:
            self.answer_sdp = None
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._hangup(self.call_id), CLOSE_STEP_S)
            raise
        self.closed = False
        self.opened_at = self.clock()
        self._recv_task = asyncio.create_task(self._receive())
        await self._seed()

    async def _seed(self) -> None:
        """What a new call starts knowing: the recent conversation, the brief and the notes of what was seen."""
        self._images, self._requested, self._response, self._speaking = [], None, None, False
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
        if self._watch_task is not None:
            self._watch_task.cancel()
        unresolved = await self._end_call()
        self.termination = "confirmed" if not unresolved else "unconfirmed: " + "; ".join(unresolved)
        self._emit(VoiceEvent("closed", data={"reason": "client"}))

    async def _end_call(self) -> list[str]:
        unresolved = []
        task, self._recv_task = self._recv_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(task, CLOSE_STEP_S)
        sb, self._sb = self._sb, None
        if sb is not None:
            try:
                await asyncio.wait_for(sb.close(), CLOSE_STEP_S)
            except Exception:
                unresolved.append("its sideband didn't close")
        if self.call_id and self._hangup is not None:
            try:
                await asyncio.wait_for(self._hangup(self.call_id), CLOSE_STEP_S)
            except Exception as exc:  # an already-ended call refuses a hangup: it is ended either way
                if "not found" not in str(exc).lower() and "404" not in str(exc):
                    unresolved.append("its WebRTC call wasn't hung up")
        self.answer_sdp = None
        return unresolved

    async def reconnect(self, offer_sdp: str | None = None, *, why: str = "close") -> float:
        """A new call from the browser's new offer (the 60-minute limit, a call that ended), or the sideband alone
        reattached to the same call; seeded as at the start. Returns the seconds it took. The same session object, so
        everything that holds it (researchers' delivery, the narrator) still reaches it."""
        started = self.clock()
        self._reconnecting = True
        mid_speech = self._speaking
        try:
            if offer_sdp:
                old = self.call_id
                await self._end_call()
                self.call_id = old  # replaced by _open_call
                await self._open_call(offer_sdp)
            else:
                task, self._recv_task = self._recv_task, None
                if task is not None:
                    task.cancel()
                sb, self._sb = self._sb, None
                if sb is not None:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(sb.close(), CLOSE_STEP_S)
                self._sb = await self._open_sideband(self.call_id)
                self._recv_task = asyncio.create_task(self._receive())
                await self._seed()
        finally:
            self._reconnecting = False
        self.reconnects += 1
        took = self.clock() - started
        self._log({"kind": "partner-reconnect", "reason": why, "closedT": self.wall() - took, "readyT": self.wall(),
                   "ms": round(took * 1000), "midSpeech": mid_speech})
        if mid_speech:
            await self.proactive("The connection was renewed while you were speaking; say only: Reconnected, go on.",
                                 event="reconnect", ref=str(self.reconnects))
        return took

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
            await self._send({"type": "conversation.item.delete", "item_id": iid})

    async def respond(self, cause: str, *, n: int | None = None, wait: bool = True, **response) -> bool:
        """Ask for a response, after the one in progress (at most RESPONSE_WAIT_S). False when it could not be asked."""
        if wait and not await self._no_response(RESPONSE_WAIT_S):
            self._log({"kind": "partner-respond-skipped", "cause": cause, "why": "a response was still running"})
            return False
        meta = {"cause": cause, "n": str(self.n if n is None else n)}
        event_id = f"rc_{uuid.uuid4().hex[:20]}"
        self._requested = {**meta, "eventId": event_id, "since": self.clock()}
        await self._send({"type": "response.create", "event_id": event_id, "response": {**response, "metadata": meta}})
        return True

    async def _no_response(self, seconds: float) -> bool:
        end = self.clock() + seconds
        while (self._requested is not None or self._response is not None) and self.clock() < end:
            if self._requested is not None and self.clock() - self._requested["since"] > RESPONSE_WAIT_S:
                self._requested = None  # asked, and nothing was made of it: none is coming
            await asyncio.sleep(0.02)
        return self._requested is None and self._response is None

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

    async def talk_down(self, n: int | None = None) -> None:
        """The talk key went down: the voice stops if it is talking (barge-in), and listening starts afresh."""
        self.n = n if isinstance(n, int) else self.n + 1
        barge = self.speaking()
        self._held, self._held_at = self.n, self.clock()
        self._log({"kind": "partner-talk", "n": self.n, "down": True, "barge": barge})
        if barge:
            await self.stop_speaking()
        await self._send({"type": "input_audio_buffer.clear"})
        self._last_user_at = self.clock()

    async def stop_speaking(self) -> None:
        cancel_at = self.wall()
        if self._response is not None or self._requested is not None:
            with contextlib.suppress(Exception):
                await self._send({"type": "response.cancel"})
        with contextlib.suppress(Exception):
            await self._send({"type": "output_audio_buffer.clear"})
        self._speaking = False
        self._emit(VoiceEvent("interrupted"))
        self._log({"kind": "partner-barge", "n": self.n, "cancelT": cancel_at, "clearT": self.wall()})

    async def talk_up(self, n: int | None = None) -> bool:
        """The talk key was released: the turn's perception goes in, the buffer is committed and an answer asked for
        at once. False when the press was a tap (nothing said): nothing is asked."""
        if self._held is None or (isinstance(n, int) and n != self._held):
            return False
        n, held_for = self._held, self.clock() - self._held_at
        self._held, self._released_at = None, self.clock()
        released = self.wall()
        self._log({"kind": "partner-talk", "n": n, "down": False})
        if held_for < MIN_HOLD_S:
            await self._send({"type": "input_audio_buffer.clear"})
            return False
        seen = self.turn_items(n)
        await self._perceived(n, seen)
        await asyncio.sleep(RELEASE_TAIL_S)
        await self._send({"type": "input_audio_buffer.commit"})
        committed = self.wall()
        self._rounds_this_turn = 0
        self._last_user_at = self.clock()
        asked = await self.respond("turn", n=n, wait=True)
        self._log({"kind": "partner-turn", "n": n, "how": "hold", "releasedT": released, "committedT": committed,
                   "responseCreatedT": self.wall() if asked else None, **seen.facts})
        return asked

    async def talk_cancel(self, n: int | None = None) -> None:
        if self._held is None:
            return
        self._log({"kind": "partner-talk", "n": self._held, "down": False, "cancel": True})
        self._held = None
        await self._send({"type": "input_audio_buffer.clear"})

    async def _perceived(self, n: int, seen: Perception) -> None:
        """This turn's perception: last turn's images deleted, this turn's text note (kept) and images (one item)."""
        old, self._images = self._images, []
        for iid in old:
            await self.delete(iid)
        if seen.note:
            self.notes.append(seen.note[:600])
            del self.notes[:-NOTES_KEPT]
            await self.add(text_item("system", seen.note))
        if seen.images:
            self._images.append(await self.add(image_item(seen.images)))

    # --- input the VoiceSession interface asks for -------------------------------------------------
    async def send_audio(self, pcm: bytes) -> None:
        return None  # over WebRTC the browser sends the microphone to OpenAI itself

    async def send_text(self, text: str) -> None:
        """A typed line while talking: a user turn like speech."""
        if self._sb is None or self.closed or not text.strip():
            return
        await self.add(text_item("user", text))
        self._last_user_at = self.clock()
        await self.respond("typed")

    async def inject(self, text: str, *, respond: bool = False, delegation_id: str | None = None,
                     source: str = "app", facts: dict | None = None) -> None:
        """Context from the app (the narrator's lines, researchers' news). respond=True: said as one line when nobody
        is talking (`proactive`); otherwise a kept system item, never answered."""
        if self._sb is None or self.closed:
            return
        if respond:
            tag = self.tag_notice() or {}
            event = (facts or {}).get("event") or tag.get("event") or (source if source != "app" else "notice")
            await self.proactive(text, event=event, ref=(facts or {}).get("ref") or tag.get("ref"),
                                 before=tag.get("before"), mark=tag.get("mark"))
            return
        await self.add(text_item("system", "Context only, not a request; do not answer it:\n" + text))
        if self.noted is not None:
            with contextlib.suppress(Exception):
                self.noted({"kind": "voice-note", "source": source, "sessionId": self.call_id, **(facts or {}),
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

    # --- lines nobody asked for --------------------------------------------------------------------
    async def proactive(self, text: str, *, event: str, ref: str | None = None,
                        before: Callable[[], Awaitable[None]] | None = None, mark: str | None = None) -> None:
        """One sentence about something that happened, said only when nobody is talking, once per event."""
        key = (event, ref or text[:80])
        now = self.clock()
        if now - self._said_events.get(key, -1e9) < SAME_EVENT_S:
            self._log({"kind": "partner-proactive", "event": event, "ref": ref, "eventT": self.wall(),
                       "userBusy": self.user_busy(), "skipped": "duplicate"})
            return
        self._said_events[key] = now
        self._waiting.append(Waiting(text, event, ref, now, before, mark))
        await self._deliver()

    async def _deliver(self) -> None:
        while self._waiting and not self.closed and self._sb is not None:
            line = self._waiting[0]
            if self.clock() - line.at > PROACTIVE_KEPT_S:
                self._waiting.pop(0)
                with contextlib.suppress(Exception):
                    await self.add(text_item("system", "Happened while the user was talking (context only): " + line.text))
                self._log({"kind": "partner-proactive", "event": line.event, "ref": line.ref, "eventT": self.wall(),
                           "userBusy": True, "skipped": "user-talking"})
                continue
            if self.user_busy() or self.speaking() or self._calls:
                return  # the watcher tries again when it is quiet
            self._waiting.pop(0)
            await self.add(text_item("system", "Something happened (facts, not a request from the user):\n" + line.text))
            if line.before is not None:
                with contextlib.suppress(Exception):
                    await line.before()
            asked = await self.respond("proactive", wait=False, tool_choice="none",
                                       instructions=self.config.system_prompt + "\n\nNow say exactly one short spoken "
                                       "sentence telling the user what just happened, from the facts above. No question, "
                                       "no offer, nothing else.")
            self.last_commentary_at = self.clock()
            self._log({"kind": "partner-proactive", "event": line.event, "ref": line.ref, "eventT": self.wall(),
                       "userBusy": False, "responseCreatedT": self.wall() if asked else None,
                       **({"markTarget": line.mark} if line.mark else {})})

    async def _watch(self) -> None:
        """Once a second or faster: waiting lines said when it is quiet; a session near the limit renewed while idle
        (asked of the route through a `go_away` event)."""
        asked_renewal = False
        while not self.closed:
            await asyncio.sleep(0.25)
            with contextlib.suppress(Exception):
                if self._waiting:
                    await self._deliver()
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
            # The connection went away under us: the route reconnects (a new offer, or the sideband again).
            self._emit(VoiceEvent("go_away", data={"reason": "close", "message": f"{type(exc).__name__}: {exc}"[:300]}))

    async def handle(self, ev: dict) -> None:
        """One server event, translated into VoiceEvents and the turn machine's state."""
        kind = ev.get("type")
        now = self.clock()
        if kind == "response.created":
            response = ev.get("response") or {}
            rid = response.get("id") or ""
            meta = response.get("metadata") or (self._requested or {})
            self._response, self._requested = rid, None
            self._causes[rid] = {"cause": meta.get("cause") or "unknown", "n": int(meta.get("n") or self.n),
                                 "createdT": self.wall()}
        elif kind in ("output_audio_buffer.started", "response.output_audio.delta"):
            rid = ev.get("response_id") or self._response or ""
            if rid and rid not in self._first_audio:
                self._first_audio[rid] = self.wall()
            if not self._speaking:
                self._speaking = True
                self._emit(VoiceEvent("vad", data={"state": "model-start"}))
            self._last_audio_at = now
            if kind == "response.output_audio.delta":
                self._emit(VoiceEvent("audio", audio=base64.b64decode(ev.get("delta") or ""), rate=RATE))
        elif kind in ("output_audio_buffer.stopped", "output_audio_buffer.cleared", "response.output_audio.done"):
            if self._speaking:
                self._speaking = False
                self._last_audio_at = now
                self._emit(VoiceEvent("turn_complete", data={"reason": "quiet"}))
        elif kind in ("response.output_audio_transcript.delta", "response.output_text.delta"):
            rid = ev.get("response_id") or self._response or ""
            self._transcripts[rid] = self._transcripts.get(rid, "") + (ev.get("delta") or "")
            self._emit(VoiceEvent("output_transcript", text=ev.get("delta") or "", data={"responseId": rid}))
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
                       ev.get("arguments") or "{}")
        elif kind == "response.output_item.done":
            item = ev.get("item") or {}
            if item.get("type") == "function_call":
                self._call(ev.get("response_id") or self._response or "", item.get("call_id") or "",
                           item.get("name") or "", item.get("arguments") or "{}")
        elif kind == "response.done":
            await self._response_done(ev.get("response") or {})
        elif kind == "error":
            error = ev.get("error") or {}
            self._emit(VoiceEvent("error", data={"message": str(error.get("message") or "")[:300],
                                                 "code": error.get("code"), "eventId": error.get("event_id")}))
            if self._requested is not None and error.get("event_id") == self._requested.get("eventId"):
                self._requested = None  # that response.create was refused: none is coming
        elif kind == "session.created":
            session = ev.get("session") or {}
            self.session_id = session.get("id") or self.session_id

    async def _open_turn(self, n: int) -> None:
        seen = self.turn_items(n)
        committed = self.wall()
        await self._perceived(n, seen)
        self._rounds_this_turn = 0
        asked = await self.respond("turn", n=n)
        self._log({"kind": "partner-turn", "n": n, "how": "open", "releasedT": None, "committedT": committed,
                   "responseCreatedT": self.wall() if asked else None, **seen.facts})

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._late.add(task)
        task.add_done_callback(self._late.discard)

    async def _response_done(self, response: dict) -> None:
        rid = response.get("id") or self._response or ""
        if rid == self._response:
            self._response = None
        if self._requested is not None and not self._causes.get(rid):
            self._requested = None
        cause = self._causes.pop(rid, {"cause": "unknown", "n": self.n, "createdT": None})
        facts = usage_facts(response.get("usage"))
        status = response.get("status")
        transcript = self._transcripts.pop(rid, "")
        if not transcript:
            for item in response.get("output") or []:
                for part in item.get("content") or [] if item.get("type") == "message" else []:
                    transcript += part.get("transcript") or part.get("text") or ""
        self._emit(VoiceEvent("usage", data={"seconds": None, "tokens": facts, "usd": usage_usd(facts, self.config.model),
                                             "responseId": rid, "cause": cause["cause"]}))
        self._emit(VoiceEvent("generation_complete", data={"responseId": rid, "status": status}))
        self._log({"kind": "partner-response", "responseId": rid, "n": cause["n"], "cause": cause["cause"],
                   "firstAudioT": self._first_audio.pop(rid, None), "audioEndT": self.wall(),
                   "cancelled": status == "cancelled", "status": status, "transcript": transcript, "usage": facts})
        calls = [item for item in response.get("output") or [] if item.get("type") == "function_call"]
        for item in calls:
            self._call(rid, item.get("call_id") or "", item.get("name") or "", item.get("arguments") or "{}")
        if self._round.get(rid):
            # Answered even when the response was cancelled (the user spoke): their next answer reads the outputs.
            self._spawn(self._finish_round(rid, cause["n"]))

    # --- tools ---------------------------------------------------------------------------------------
    def _call(self, rid: str, call_id: str, name: str, arguments: str) -> None:
        if not call_id or call_id in self._seen_calls:
            return
        self._seen_calls.add(call_id)
        try:
            args = json.loads(arguments) if arguments else {}
        except ValueError:
            args = {}
        self._round.setdefault(rid, []).append(call_id)
        event = VoiceEvent("tool_call", call_id=call_id, name=name, args=args if isinstance(args, dict) else {})
        self._emit(event)
        self._calls[call_id] = asyncio.ensure_future(self._run_tool(event))

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
        return result

    async def _finish_round(self, rid: str, n: int) -> None:
        """Every call of one response answered (as each is ready), then the model asked to go on: once, and only when
        the user has not started a newer turn meanwhile (their turn's answer will read these outputs)."""
        ids = self._round.pop(rid, [])
        tasks = {cid: self._calls[cid] for cid in ids if cid in self._calls}
        progress_at = self.clock() + PROGRESS_AFTER_S
        pending = set(tasks.values())
        looked = False
        sent: set[str] = set()
        while pending:
            done, pending = await asyncio.wait(pending, timeout=0.1)
            for cid, task in tasks.items():
                if task in done and cid not in sent:
                    sent.add(cid)
                    looked |= await self._output(cid, task)
            if pending and self.clock() > progress_at and rid not in self._progress_said and not self.speaking() \
                    and self._held is None and n == self.n:
                self._progress_said.add(rid)
                await self.respond("progress", n=n, wait=False, tool_choice="none",
                                   instructions=self.config.system_prompt + "\n\nThe app is still working on your last "
                                   "tool call. Say only a short progress phrase, at most six words, without "
                                   "claiming anything is done.")
        for cid in ids:
            self._calls.pop(cid, None)
        self._progress_said.discard(rid)
        if self.closed or self._held is not None or n != self.n:
            return  # the user is talking again: their turn's answer reads these outputs
        self._rounds_this_turn += 1
        if self._rounds_this_turn > TOOL_ROUNDS:
            self._log({"kind": "partner-rounds-stopped", "n": n, "rounds": self._rounds_this_turn})
            await self.respond("tool", n=n, tool_choice="none")
            return
        await self.respond("look" if looked else "tool", n=n)

    async def _output(self, call_id: str, task: asyncio.Task) -> bool:
        """One call's output into the conversation; a fresh look after a change on screen goes in beside it."""
        try:
            result = task.result()
        except BaseException as exc:  # noqa: BLE001 - a cancelled or broken handler is said as such
            result = {"ok": False, "error": f"the tool did not finish ({type(exc).__name__})"}
        look = result.pop("_look", None)
        output = json.dumps(result, ensure_ascii=False, default=str)[:12000]
        await self.add({"id": item_id("fo"), "type": "function_call_output", "call_id": call_id, "output": output})
        if isinstance(look, dict) and (look.get("images") or look.get("text")):
            note = str(look.get("text") or "")
            if note:
                await self.add(text_item("system", note))
            if look.get("images"):
                self._images.append(await self.add(image_item(look["images"])))
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
    audio delta (docs/bench/realtime-partner.md)."""
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
