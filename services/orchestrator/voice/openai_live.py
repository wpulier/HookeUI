"""OpenAI GPT-Live (`gpt-live-1`) as a voice session, on the same scene tools as Gemini Live.

GPT-Live hears and speaks but takes no images, so it uses *client delegation*: when the
user asks about the view, the model emits `session.delegation.created` (an id, no task
text), keeps talking ("let me look..."), and the app does the work. The task is what the
user said since the previous delegation (the input transcript), as the delegation guide
recommends. The first tool in the config handles it; its result goes back with
`session.commentary.append`, which GPT-Live paraphrases aloud.

Following OpenAI's Live guides (developers.openai.com/api/docs/guides/live*):
  - what the session should know (the conversation, the day, the view) is given at startup as
    `session.input` history, not as a context update after it has started;
  - a delegation's answer is said as soon as the brain has written it (`say`, a first
    `session.commentary.append`), and what its tools did follows in a second one;
  - delegated work is never cancelled when the session closes: its result still reaches the chat;
  - muting uses `session.input_audio.mute`, so the session knows it is muted;
  - silent context (`session.thinking.append`) carries an event id, and its acknowledgement is
    reported, so whether a note set the model talking can be measured; each one is logged whole as a
    `voice-note` record (`noted`: its text, where it came from, the session id), so what the voice held
    when it answered can be read back.

Two links (option VOICELINK):
  - "relay": the browser streams the microphone to us over a WebSocket and we relay it, and the
    model's speech back, over a primary Live WebSocket (session.start);
  - "webrtc" (OpenAI's recommended browser transport, voice-webrtc and voice-server-controls
    guides): the browser and OpenAI exchange audio directly over WebRTC (its own echo cancelling
    and timing); we make the session from the browser's SDP offer (`client.live.create`), keep
    the API key here, and attach a trusted sideband (`client.live.sideband.connect`) that gets the
    same transcripts, delegations and reflected audio and sends the same commentary and context.
    The browser's data channel is given no events at all (an untrusted frontend).

Protocol differences hidden here, so the route and studio treat both providers alike:
  - audio is 24 kHz both ways; microphone audio at 16 kHz (older studios) is resampled.
  - transcripts arrive as fragments with no "final" marker: the user's transcript is
    reported as interim until the model starts answering or delegates.
  - there is no interruption or turn-complete event, and output audio streams continuously
    (silence included): user speech that starts while the model is audibly speaking is
    reported as `interrupted`, and the model's turn is complete when the user speaks again
    or the model has been quiet for a moment.

Turn-taking is GPT-Live's own (checked against OpenAI's documentation on 2026-10-28, for Part 2.5): the session
reference (developers.openai.com/api/reference/resources/live: model, audio, client, delegation, input,
instructions, store, triggers), the Live guides (developers.openai.com/api/docs/guides/live,
live-conversations, live-migration, voice-websockets) and the prompting guide (guides/live-prompting)
document no `turn_detection`, `server_vad`/`semantic_vad`, `eagerness`, `interrupt_response` or `create_response`
for `gpt-live-1`: "GPT-Live decides when to speak; remove manual audio commits and voice-turn triggers"
(live-migration), "Transcript deltas have no item ID or event that marks a completed conversational turn" and
"Gaps between transcript events alone do not establish silence" (live-conversations). Yielding is shaped only by
the instructions (the voice prompt's Interruption section follows the prompting guide's recipe). So nothing is
configured here and the app's own guesses stay as they were (QUIET_TURN_END_S, SPEECH_PEAK): they decide only the
app's bookkeeping (captions, `interrupted` for the relay's playback, when it is idle), never when the model speaks.
What the app asks the voice to say waits while the user's words are still coming (`_hold_for_user`); what they say
meanwhile never turns it into silent context.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import uuid
import os
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

import numpy as np

from ..brain.claims import DOING
from ..brain.narrator import Said
from .base import VoiceConfig, VoiceEvent, VoiceSession

RATE = 24000
QUIET_TURN_END_S = 1.2  # no model speech for this long after it spoke ends the model's turn
# The WebSocket streams output audio continuously, silence included (measured: 100 ms
# chunks with peak 0-50 while idle), so speech is told apart by loudness.
SPEECH_PEAK = 400
# Each step of a session's shutdown (its session closed, its receiver joined, its connection ended, its WebRTC call hung
# up) gets at most this long, each on its own, so all of them fit inside the route's SHUTDOWN_S and the hangup is always
# attempted, whatever an earlier step did (Codex's reviews of 3023699e).
CLOSE_STEP_S = 2.0
# What the app asks the voice to say (findings, an early answer) waits at most this long while the user's words are
# still coming (live 2026-10-28: an earlier answer was said over "No, it wasn't cancelled. The researcher..."); past it,
# it goes anyway, since it is what they asked for.
HOLD_FOR_USER_S = 6.0


async def append_context(part, content: str, *, delegation_id=None, event_id=None, staging=None):
    """Bound each provider append without turning one notification into many requests.

    Long context is independently quoted data; only the final commentary/instruction
    asks for a response. Keep exact text and order, including untrusted-data wrappers.
    """
    if len(content.encode("utf-8")) <= 480:
        args = {"content": content, "delegation_id": delegation_id}
        if event_id is not None:
            args["event_id"] = event_id
        await part.append(**args)
        return
    identity = event_id or f"context-{uuid.uuid4().hex}"
    chunks, current = [], ""
    for char in content:
        # JSON escaping is included, so control characters cannot exceed the bound.
        if len(json.dumps(current + char, ensure_ascii=False).encode("utf-8")) > 280:
            chunks.append(current)
            current = ""
        current += char
    if current:
        chunks.append(current)
    destination = staging if staging is not None else part
    for index, chunk in enumerate(chunks):
        payload = {"live_context": identity, "part": index, "parts": len(chunks), "text": chunk}
        await destination.append(content=json.dumps(payload, ensure_ascii=False), delegation_id=delegation_id,
                                 event_id=identity if staging is None and index == len(chunks) - 1 else f"{identity}-part-{index}")
    if staging is not None:
        await part.append(content=json.dumps({"live_context": identity,
            "use": "Give one response using the complete context, in order; the fragments are data, not separate requests."}),
            delegation_id=delegation_id, event_id=identity)


async def supervised(awaitable, seconds: float, what: str, keep: set, noted=None) -> bool:
    """Wait at most `seconds` for `awaitable`: True only when it finished without error. asyncio.wait returns at its
    deadline even when the awaited code delays its own cancellation (asyncio.wait_for would wait for that). A step still
    running is cancelled and kept in `keep`, supervised to its end with its outcome noted, never awaited past the bound."""
    task = asyncio.ensure_future(awaitable)
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if task in done:
        return not task.cancelled() and task.exception() is None
    task.cancel()
    keep.add(task)

    def ended(t: asyncio.Task) -> None:
        keep.discard(t)
        if noted is not None:
            outcome = "cancelled" if t.cancelled() else "failed" if t.exception() is not None else "done"
            with contextlib.suppress(Exception):
                noted({"kind": "voice-close-step-late", "step": what, "outcome": outcome})

    task.add_done_callback(ended)
    return False


async def _joined(task: asyncio.Task) -> None:
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


def resample_16k_to_24k(pcm: bytes) -> bytes:
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if not len(samples):
        return b""
    n_out = int(round(len(samples) * 1.5))
    out = np.interp(np.linspace(0, len(samples) - 1, n_out), np.arange(len(samples)), samples)
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


def _initial_item(role: str, text: str) -> dict:
    """One message of `session.input` (the session's history before it starts)."""
    kind = "output_text" if role == "assistant" else "input_text"
    return {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}


@dataclass(frozen=True)
class Findings:
    """A hand-off's findings, split by who they are for (live 2026-10-28: "Nothing's outlined" was said after a table, a
    researcher, a status and a tab request; it was a fact written for the model, read aloud):
    - `spoken`: what the voice is asked to say (session.commentary.append, paraphrased aloud);
    - `facts`: what only the model should know (session.thinking.append on the delegation, never asked to be said):
      steps still running, nothing outlined;
    - `outcomes`: the identities of what `spoken` reports (brain/narrator.Said), so it is said once."""
    spoken: str = ""
    facts: str = ""
    outcomes: frozenset = frozenset()


def _outlining(tool) -> bool:
    """A step that makes outlines on the view, by what it declares it makes (produces.py: named outlines, cells)."""
    from ..produces import TOOLS, kinds_of

    return bool({"named-outline", "population"} & set(kinds_of(TOOLS.get(str(tool or "")))))


def split_findings(result: dict[str, Any], said: str = "", request_id: str | None = None, already=()) -> Findings:
    """What the app found for one spoken request (`request_id`, else the result's `requestId`), as what the voice says
    and what only the model knows (max ~500 tokens). When the answer was already said (`said`, sent early), only what
    happened since: the outcomes, and the answer again only if the app rewrote it (a step failed, nothing came of it).
    `already` (a Said, or a set): identities said before, by any path; their parts are left out of `spoken`."""
    rid = request_id or result.get("requestId") or None

    def action_key(a: dict) -> str | None:
        if a.get("actionId"):
            return f"action:{a['actionId']}"
        return f"action:{rid}:{a.get('tool')}:{a.get('summary')}" if rid else None

    def new(key) -> bool:
        return key is None or key not in already

    if result.get("error"):
        key = f"error:{rid}" if rid else None
        return Findings(f"The app could not look at the view: {result['error']}" if new(key) else "",
                        outcomes=frozenset({key} - {None}))
    if result.get("unmatched"):
        doing = "; ".join(str(w) for w in (result.get("inProgress") or [])[:3])
        return Findings("The app heard no new request for this, so it started nothing new. If the user asked for "
                        "something, ask them to say it again in a few words.",
                        f"Still being done ({DOING}): {doing}." if doing else "")
    answer = str(result.get("answer") or "").strip()
    actions = result.get("actions") or []
    outlined = [str(x) for x in result.get("outlined") or [] if x]
    done = [a for a in actions if a.get("ok") and a.get("summary")]
    failed = [a for a in actions if a.get("ok") is False and a.get("summary")]
    doing = [str(a.get("tool")) + (f" ({a['progress']})" if a.get("progress") else "")
             for a in actions if a.get("ok") is None]
    spoken: list[str] = []
    facts: list[str] = []
    outcomes: set[str] = set()
    # What was already said stands unless the receipts contradict it (a step failed, or nothing came of it):
    # a reworded answer is not said again as a correction (live 2026-10-10: 6 of 7 replies were re-said).
    reworded = bool(said) and answer and " ".join(answer.split()) != " ".join(said.split())
    contradicted = bool(failed) or not (outlined or done or doing)
    answer_key = f"answer:{rid}" if rid else None
    settled = {action_key(a) for a in actions if a.get("ok") is not None} - {None}
    # The answer to a request whose every settled step was said already (its late result, another hand-off) says
    # nothing new: its outcome was said.
    told = (answer_key is not None and answer_key in already) or (bool(settled) and not any(new(k) for k in settled))
    if reworded and contradicted:
        key = f"correction:{rid}" if rid else None
        if new(key):
            spoken.append(f"Correction to what you said: {answer}")
            outcomes.add(key)
    elif answer and not said and not told:
        spoken.append(answer)
        outcomes.add(answer_key)
    if outlined:
        key = f"outlined:{rid}:" + ",".join(sorted(str(i) for i in result.get("outlinedIds") or outlined)) if rid else None
        if new(key):
            spoken.append(f"Outlined on screen: {', '.join(outlined[:8])}.")
            outcomes.add(key)
    # "Nothing outlined" is a fact only for a request that asked for outlines (its look's targets, or a step that makes
    # them) and got none; the voice says it once, with what to try next. A step that failed says it itself.
    asked = int(result.get("targets") or 0) > 0 or any(_outlining(a.get("tool")) for a in actions)
    made = bool(outlined) or any(a.get("ok") is not False and _outlining(a.get("tool")) for a in actions)
    if asked and not made:
        facts.append("Nothing was outlined for this request.")
        key = f"nothing-outlined:{rid}" if rid else None
        if not any(a.get("ok") is False and _outlining(a.get("tool")) for a in actions) and new(key):
            spoken.append("Nothing could be outlined for this request: say so once, with the next useful thing to try.")
            outcomes.add(key)
    fresh = [a for a in done if new(action_key(a))]
    if fresh:
        spoken.append(f"Done on screen: {'; '.join(str(a['summary']) for a in fresh[:4])}.")
        outcomes |= {action_key(a) for a in fresh}
    if doing:
        # Still running when the findings went out: the app says when it lands (brain/claims.py). A wait is never said.
        facts.append(f"Still being done ({DOING}): {'; '.join(doing[:4])}.")
    fresh = [a for a in failed if new(action_key(a))]
    if fresh:
        said_failed = "; ".join(str(a["summary"]) + (f" (next: {a['next']})" if a.get("next") else "") for a in fresh[:4])
        spoken.append(f"Could not: {said_failed}. Tell the user plainly what did not work.")
        outcomes |= {action_key(a) for a in fresh}
    if not (answer or outlined or actions or said or asked):
        key = f"nothing:{rid}" if rid else None
        if new(key):
            spoken.append("The app has no answer and did nothing for this request. Tell the user plainly, in a few words.")
            outcomes.add(key)
    return Findings(" ".join(spoken)[:1800], " ".join(facts)[:900], frozenset(outcomes - {None}))


def findings_text(result: dict[str, Any], said: str = "") -> str:
    """What the voice is asked to say of a hand-off's findings (`split_findings(...).spoken`)."""
    return split_findings(result, said=said).spoken


def only_in_progress(result: dict[str, Any]) -> bool:
    """A hand-off's result that holds nothing but steps still running: nothing outlined, done or failed, no error."""
    actions = result.get("actions") or []
    # A step waiting on something (a model loading, a slow search) is shown on its row in the chat, never said: the
    # user's 2026-10-11 decision, one acknowledgement, the card, then the outcome ("It's still being done" was noise).
    return (not result.get("error") and not result.get("unmatched") and not result.get("outlined")
            and bool(actions) and all(a.get("ok") is None for a in actions))


class OpenAILiveSession(VoiceSession):
    def __init__(self, config: VoiceConfig, *, api_key: str | None = None, connect: Callable[..., Any] | None = None,
                 link: str = "relay", create: Callable[..., Any] | None = None, sideband: Callable[..., Any] | None = None,
                 hangup: Callable[..., Any] | None = None, said: Said | None = None) -> None:
        """`connect()` returns an async context manager yielding a live connection with
        `.session.start/close`, `.session.input_audio.append`, `.session.commentary.append`,
        `.session.thinking.append`, `.session.instructions.append` and async iteration over
        server events; defaults to the OpenAI SDK's client.live.connect. Tests pass a fake."""
        self.config = config
        self.browser_client = None  # bound by the accepted Studio voice route
        self._connect = connect
        self.link = link  # "relay" | "webrtc"
        self._create, self._sideband = create, sideband  # tests pass fakes for client.live.create / sideband.connect
        self._hangup = hangup  # client.live.sessions.hangup: a created session ended when what follows fails
        self._hung_up = False
        self.termination = ""  # after close: "confirmed", or "unconfirmed: why" (a step that didn't end in its time)
        self._late: set = set()  # shutdown steps still running past their bound, supervised to their end
        self._shutting: asyncio.Task | None = None  # its one shutdown: every close waits for the same outcome
        self.answer_sdp: str | None = None  # webrtc: the SDP answer for the browser
        self.session_id: str | None = None
        self._api_key = api_key
        self._cm = None
        self._conn = None
        self._queue: asyncio.Queue[VoiceEvent] = asyncio.Queue()
        self._recv_task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None
        self._tool_tasks: dict[str, asyncio.Task] = {}
        self._heard = ""  # user speech since the model last answered (interim transcript)
        self._heard_ms: tuple[float | None, float | None] = (None, None)  # its span on the server's clock (start, end)
        self._task_text = ""  # user speech since the last delegation: shown with the hand-off, never what is done
        self._last_audio_at = 0.0
        self._last_user_at = 0.0
        self._model_speaking = False
        self._barged = False  # the user has spoken over the model's current speech (said once per speech)
        self._notes = 0
        self._event_ids: set[str] = set()
        self._event_order: list[str] = []
        self._started_wall = time.time()
        # The interaction log (the route sets it): every silent note is written there as it is sent (`voice-note`).
        self.noted: Callable[[dict], None] | None = None
        self.last_commentary_at = 0.0  # when the app last gave the model something to say
        # What the voice was asked to say, by identity (brain/narrator.Said): the brain's, shared by every Talk session
        # (voice/route.py), so an outcome is said once per request whichever path, delegation or session reports it.
        self.said = said if said is not None else Said()
        self.spoken_actions: set[str] = self.said.actions  # steps whose outcome the voice was asked to say
        self.requests = None  # brain/speech.SpokenRequests: which request a delegation is (route.py sets it)
        self.closed = False
        self._closed_ack = asyncio.Event()
        self.usage_seconds = None  # cumulative provider total retained after the UI event pump stops

    # --- lifecycle -------------------------------------------------------------------
    def _session_config(self, media: bool) -> dict[str, Any]:
        session: dict[str, Any] = {"model": self.config.model, "delegation": {"type": "client"}}
        if not media:
            session["audio"] = {"format": {"type": "audio/pcm", "rate": RATE}}
        if self.config.system_prompt:
            session["instructions"] = self.config.system_prompt
        if self.config.voice:
            session.setdefault("audio", {})["output"] = {"voice": self.config.voice}
        if self.config.history:
            session["input"] = [_initial_item(role, text) for role, text in self.config.history if text.strip()][-128:]
        if media:
            # The browser is an untrusted frontend: its data channel may send and receive nothing;
            # everything goes through our sideband (mute included).
            session["client"] = {"data_channel": {"allowed_client_events": [], "allowed_server_events": []}}
        return session

    async def start(self, offer_sdp: str | None = None) -> None:
        if self.link == "webrtc":
            await self._start_webrtc(offer_sdp)
            return
        connect = self._connect
        if connect is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=self._api_key or os.environ["OPENAI_API_KEY"], max_retries=0)
            connect = client.live.connect
        self._cm = connect()
        self._conn = await self._cm.__aenter__()
        await self._conn.session.start(session=self._session_config(media=False))
        self.closed = False
        self._recv_task = asyncio.create_task(self._receive_loop())
        self._watch_task = asyncio.create_task(self._watch_turns())

    async def _start_webrtc(self, offer_sdp: str | None) -> None:
        if not offer_sdp:
            raise ValueError("a WebRTC voice session needs the browser's SDP offer")
        create, sideband, hangup = self._create, self._sideband, self._hangup
        if create is None or sideband is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=self._api_key or os.environ["OPENAI_API_KEY"], max_retries=0)
            create = create or client.live.create
            sideband = sideband or client.live.sideband.connect
            hangup = hangup or client.live.sessions.hangup
        self._hangup = hangup
        created = await create(session=self._session_config(media=True), transport={"type": "webrtc", "sdp": offer_sdp})
        get = (lambda o, k: o.get(k)) if isinstance(created, dict) else (lambda o, k: getattr(o, k))
        self.session_id = get(get(created, "session"), "id")
        self.answer_sdp = get(get(created, "transport"), "sdp")
        # Attached at once, so the sideband sees the conversation from its start (server-controls guide). A created
        # session whose sideband couldn't attach is ended (hangup) and its answer never reaches the browser.
        self._cm = sideband(session_id=self.session_id)
        try:
            self._conn = await self._cm.__aenter__()
        except BaseException:
            self._cm = None
            await self._end_created()
            raise
        self.closed = False
        self._started_at = time.monotonic()
        self._recv_task = asyncio.create_task(self._receive_loop())
        self._watch_task = asyncio.create_task(self._watch_turns())
        self._emit(VoiceEvent("ready", data={"session_id": self.session_id, "link": "webrtc"}))

    async def close(self) -> None:
        """Its one shutdown, whoever asks and however often: a second close (the cap's, then the route's) waits for the
        same outcome, and its `termination` stays what that shutdown found (Codex's review: a repeated close once read a
        failed hangup's guard as success)."""
        first = self._shutting is None
        if first:
            self._shutting = asyncio.ensure_future(self._shutdown())
        await asyncio.shield(self._shutting)
        if first:
            self._emit(VoiceEvent("closed", data={"reason": "client"}))

    async def _end_created(self) -> bool:
        """A WebRTC session created here is ended at the provider (said in its log line if it fails): the answer is
        withheld, so no browser joins it either. True when there was none to end or its hangup returned in its time."""
        if self.link != "webrtc" or not self.session_id:
            return True  # no call created here: nothing to end
        if self._hung_up:
            return True  # ended already, by a hangup that returned
        self.answer_sdp = None
        if self._hangup is None:
            return False  # no way to end it from here: unknown, never success
        ended = await supervised(self._hangup(self.session_id), CLOSE_STEP_S, "hangup", self._late, self.noted)
        if ended:
            self._hung_up = True  # only a hangup that returned ends it
        elif self.noted is not None:
            self.noted({"kind": "voice-hangup-failed", "sessionId": self.session_id})
        return ended

    async def _shutdown(self) -> None:
        self.closed = True
        # Delegated work is not cancelled (the docs: finish delegated work; the user may stop
        # talking right after asking): it carries on and its result reaches the chat; only its
        # commentary is skipped once the session is gone.
        if self._watch_task is not None:
            self._watch_task.cancel()
        # Each step bounded on its own (CLOSE_STEP_S) and the hangup attempted whatever came before: a sideband close that
        # stalls once kept the WebRTC call from ever being ended. What isn't confirmed is said (`termination`).
        unresolved = []
        if self._conn is not None and not await supervised(self._close_and_ack(), CLOSE_STEP_S, "session close",
                                                           self._late, self.noted):
            unresolved.append("its session didn't close")
        if self._recv_task is not None:
            self._recv_task.cancel()
            await supervised(_joined(self._recv_task), CLOSE_STEP_S, "receiver", self._late, self.noted)
        if self._cm is not None and not await supervised(self._cm.__aexit__(None, None, None), CLOSE_STEP_S,
                                                         "connection", self._late, self.noted):
            if self.noted is not None:
                self.noted({"kind": "voice-transport-cleanup-incomplete", "step": "connection"})
            if not self._closed_ack.is_set():
                unresolved.append("its connection didn't end")
        self._cm = self._conn = self._recv_task = self._watch_task = None
        # session.closed is the provider's finalization receipt, including when the
        # primary WebRTC connection ended first. Do not hang up the already-ended
        # call or downgrade that receipt because subsequent transport cleanup failed.
        # https://developers.openai.com/api/docs/guides/live-conversations#usage-and-graceful-close
        if self._closed_ack.is_set():
            self._hung_up = True
            self.answer_sdp = None
            unresolved.clear()
        elif not await self._end_created():  # no final receipt: try ending the call itself
            unresolved.append("its WebRTC call wasn't hung up")
        self.termination = "confirmed" if not unresolved else "unconfirmed: " + "; ".join(unresolved)
        if unresolved and self.noted is not None:
            with contextlib.suppress(Exception):
                self.noted({"kind": "voice-termination-unconfirmed", "sessionId": self.session_id, "why": self.termination})

    async def _close_and_ack(self) -> None:
        # A successful WebSocket send is not confirmation that the provider stopped.
        # Keep receiving final usage until session.closed, inside the existing close bound.
        if not self._closed_ack.is_set():
            await self._conn.session.close()
            await self._closed_ack.wait()

    # --- input -----------------------------------------------------------------------
    async def send_audio(self, pcm: bytes) -> None:
        if self._conn is None or self.closed or self.link == "webrtc":
            return  # over WebRTC the browser sends the microphone to OpenAI itself
        audio = pcm if self.config.input_rate == RATE else resample_16k_to_24k(pcm)
        if audio:
            await self._conn.session.input_audio.append(audio=base64.b64encode(audio).decode())

    async def send_text(self, text: str) -> None:
        # GPT-Live takes no typed user turns; trusted instructions are the closest input.
        if self._conn is not None and not self.closed:
            await append_context(self._conn.session.instructions, f"The user typed: {text}", staging=self._conn.session.thinking)

    async def inject(self, text: str, *, respond: bool = False, delegation_id: str | None = None,
                     source: str = "app", facts: dict | None = None) -> None:
        """respond=True: GPT-Live says it (paraphrased; on the delegation it belongs to when
        given, which reads as a natural follow-up). Otherwise silent context, logged as a voice-note
        record with `source` (view, recap, hands, narrator...) and the caller's `facts` (never sent)."""
        if self._conn is None or self.closed:
            return
        if respond:
            self.last_commentary_at = time.monotonic()
            await append_context(self._conn.session.commentary, text, delegation_id=delegation_id, staging=self._conn.session.thinking)
        else:
            await self._think("Silent context update, not a user request. Do not acknowledge it or announce readiness. "
                              "An observation or window listing is not confirmation that a requested action completed.\n"
                              + text, source=source, facts=facts)

    async def _think(self, content: str, *, source: str, delegation_id: str | None = None,
                     facts: dict | None = None) -> None:
        """One piece of silent context (session.thinking.append, with its event id), logged whole as it was sent
        (`voice-note`: the text, where it came from, the session, and what the caller adds, such as a view note's
        look and frame ages), so what the voice held when it answered is on the record. A send that fails is logged
        with its error and raised as before."""
        self._notes += 1
        event_id = f"note-{self._notes}"
        error = None
        try:
            await append_context(self._conn.session.thinking, content, delegation_id=delegation_id, event_id=event_id)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            if self.noted is not None:
                with contextlib.suppress(Exception):  # a log that fails never costs the note
                    self.noted({"kind": "voice-note", "source": source, "eventId": event_id, "sessionId": self.session_id,
                                **({"delegationId": delegation_id} if delegation_id else {}), **(facts or {}),
                                "chars": len(content), "text": content, **({"error": error} if error else {})})

    async def mute(self, muted: bool) -> None:
        """The user muted the microphone: the session is told, rather than just hearing nothing."""
        if self._conn is None or self.closed:
            return
        with contextlib.suppress(Exception):
            if muted:
                await self._conn.session.input_audio.mute()
            else:
                await self._conn.session.input_audio.unmute()

    def idle_since(self) -> float:
        """Seconds since anyone last spoke (model speech or user speech); 0 while speaking."""
        if self._model_speaking or self._heard.strip():
            return 0.0
        last = max(self._last_audio_at, self._last_user_at)
        return time.monotonic() - last if last else float("inf")

    def user_speaking(self) -> bool:
        """The user is talking and the model is not: silent context sent now lands before the reply."""
        return bool(self._heard.strip()) and not self._model_speaking

    def user_talking(self) -> bool:
        """The user's words are still coming: heard since the voice last answered, the last within QUIET_TURN_END_S
        (time-bounded, unlike user_speaking, which holds until the model answers)."""
        return self.user_speaking() and time.monotonic() - self._last_user_at < QUIET_TURN_END_S

    async def _hold_for_user(self) -> bool:
        """What the app asks the voice to say waits while the user's words are still coming, at most HOLD_FOR_USER_S
        (GPT-Live itself decides when to speak; this only keeps the app from handing it words to say over the user's).
        True when it waited."""
        waited, until = False, time.monotonic() + HOLD_FOR_USER_S
        while self.user_talking() and not self.closed and time.monotonic() < until:
            waited = True
            await asyncio.sleep(0.1)
        return waited

    def adopt_said(self, said: Said) -> None:
        """Share the brain's record of what was said (a new Talk session knows what the last one said)."""
        self.said, self.spoken_actions = said, said.actions

    def _request_of(self, did: str | None, result: dict | None = None) -> str:
        """The spoken request a delegation is (its admission's id: brain/speech.SpokenRequests.request_for), else the
        delegation itself: what is said is said once per this identity."""
        rid = (result or {}).get("requestId")
        if not rid and self.requests is not None and hasattr(self.requests, "request_for"):
            rid = self.requests.request_for(did)
        return rid or f"delegation:{did}"

    def last_speech_at(self) -> float:
        return self._last_audio_at

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
            except Exception:
                pass

    def handle(self, ev: Any) -> None:
        """Translate one server event (SDK model or dict) into VoiceEvents."""
        get = (lambda k, d=None: ev.get(k, d)) if isinstance(ev, dict) else (lambda k, d=None: getattr(ev, k, d))
        event_id = get("event_id")
        if event_id:
            if event_id in self._event_ids:
                return
            self._event_ids.add(event_id)
            self._event_order.append(event_id)
            if len(self._event_order) > 4096:
                self._event_ids.discard(self._event_order.pop(0))
        kind = get("type")
        now = time.monotonic()
        if kind == "session.started":
            session = get("session")
            sid = session.get("id") if isinstance(session, dict) else getattr(session, "id", None)
            self.session_id = self.session_id or sid  # a relay session learns its id here (voice-note records carry it)
            self._started_at = now  # the server's times (start_ms, end_ms, offset_ms) count from here
            self._emit(VoiceEvent("ready", now, data={"session_id": sid}))
        elif kind == "session.output_audio.delta":
            audio = base64.b64decode(get("delta") or "")
            samples = np.frombuffer(audio[: len(audio) // 2 * 2], np.int16)
            if len(samples) and int(np.abs(samples.astype(np.int32)).max()) >= SPEECH_PEAK:
                self._finish_heard()
                self._last_audio_at = now
                self._model_speaking = True
            if self.link != "webrtc":  # over WebRTC the browser plays the model's speech itself
                self._emit(VoiceEvent("audio", now, audio=audio, rate=RATE))
        elif kind == "session.output_transcript.delta":
            self._finish_heard()
            self._emit(VoiceEvent("output_transcript", now, text=get("delta") or "",
                                  data=self._timeline(now, start_ms=get("start_ms"), end_ms=get("end_ms"))))
        elif kind == "session.input_transcript.delta":
            text = get("delta") or ""
            if self._model_speaking and not self._barged:
                # The user started talking while the model was speaking: barge-in, said once. The model's turn
                # still ends only when its audio stops (`_watch_turns`): it may stop, cut off, and answer anew, or
                # talk on through it ("Yes," ... "the tools I have can't run ..."); only its audio tells which.
                self._barged = True
                if now - self._last_audio_at < 1.0:
                    self._emit(VoiceEvent("interrupted", now))
                self._emit(VoiceEvent("turn_complete", now, data={"reason": "user_spoke"}))
            self._heard += text
            self._task_text += text
            self._last_user_at = now
            start, end = get("start_ms"), get("end_ms")
            first, last = self._heard_ms
            self._heard_ms = (first if first is not None else start if isinstance(start, (int, float)) else None,
                              end if isinstance(end, (int, float)) else last)
            self._emit(VoiceEvent("input_transcript", now, text=self._heard.strip(),
                                  data={"interim": True, "delta": text, **self._timeline(now, start_ms=get("start_ms"), end_ms=get("end_ms"))}))
        elif kind == "session.delegation.created":
            self._finish_heard()
            delegation = get("delegation")
            did = delegation.get("id") if isinstance(delegation, dict) else delegation.id
            task, self._task_text = self._task_text.strip(), ""
            # Which request this is, is the app's: the delegation's place on the server's clock (offset_ms) is
            # matched to the spans of the requests heard (brain/speech.py), never to the words joined here.
            offset = get("offset_ms")
            call = VoiceEvent("tool_call", now, call_id=did, name=self._tool_name(),
                              args={"question": task, "delegation_id": did,
                                    "offset_ms": offset if isinstance(offset, (int, float)) else None},
                              data=self._timeline(now, offset_ms=offset))
            self._emit(call)
            self._tool_tasks[did] = asyncio.create_task(self._run_delegation(call))
        elif kind == "session.thinking.appended":
            self._emit(VoiceEvent("note_ack", now, data={"eventId": get("client_event_id"),
                                                          **self._timeline(now, start_ms=get("start_ms"), end_ms=get("end_ms"))}))
        elif kind == "session.usage.updated":
            usage = get("usage")
            self.usage_seconds = usage.get("seconds") if isinstance(usage, dict) else getattr(usage, "seconds", None)
            self._emit(VoiceEvent("usage", now, data={"seconds": self.usage_seconds}))
        elif kind == "error":
            error = get("error")
            message = error.get("message") if isinstance(error, dict) else getattr(error, "message", str(error))
            code = error.get("code") if isinstance(error, dict) else getattr(error, "code", None)
            self._emit(VoiceEvent("error", now, data={"message": str(message)[:300], "code": code}))
        elif kind == "session.closed":
            usage = get("usage")
            seconds = usage.get("seconds") if isinstance(usage, dict) else getattr(usage, "seconds", None)
            if isinstance(seconds, (int, float)):
                self.usage_seconds = seconds
                self._emit(VoiceEvent("usage", now, data={"seconds": seconds}))
            self._closed_ack.set()
            self.closed = True
            self._emit(VoiceEvent("closed", now, data={"reason": get("reason") or "server"}))

    def _timeline(self, now: float, **server_ms) -> dict:
        """The server's times for an event (ms from session start), with how far each lags our
        own clock when it arrives (lagMs: arrival minus the server's time). Recorded, not used:
        the 2026-10 spike found they step in 200 ms and drift (docs/bench/spike-s2-gpt-live.md);
        make voice-report measures whether they can be trusted."""
        out = {"speechSession": self.session_id}
        start = server_ms.get("start_ms")
        if isinstance(start, (int, float)):
            out["at"] = self._started_wall + start / 1000
        started = getattr(self, "_started_at", None)
        for name, value in server_ms.items():
            if isinstance(value, (int, float)):
                key = "".join(w.capitalize() if i else w for i, w in enumerate(name.split("_")))  # start_ms -> startMs
                out[key] = value
                if started is not None:
                    out[f"{key[:-2]}LagMs"] = round((now - started) * 1000 - value)
        return out

    def _finish_heard(self) -> None:
        if self._heard.strip():
            start, end = self._heard_ms
            self._emit(VoiceEvent("input_transcript", text=self._heard.strip(),
                                  data={"startMs": start, "endMs": end} if start is not None or end is not None else {}))
        self._heard, self._heard_ms = "", (None, None)

    async def _receive_loop(self) -> None:
        try:
            async for ev in self._conn:
                self.handle(ev)
                if self._closed_ack.is_set():
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._emit(VoiceEvent("error", data={"message": f"{type(exc).__name__}: {exc}"[:300]}))
        if not self.closed:
            self.closed = True
            self._emit(VoiceEvent("closed", data={"reason": "connection ended"}))

    async def _watch_turns(self) -> None:
        """No turn events in this protocol: the model's turn ends when its audio stops."""
        while not self.closed:
            await asyncio.sleep(0.2)
            if self._model_speaking and time.monotonic() - self._last_audio_at > QUIET_TURN_END_S:
                self._model_speaking = self._barged = False
                self._emit(VoiceEvent("turn_complete", data={"reason": "quiet"}))

    # --- delegation ------------------------------------------------------------------
    def _tool_name(self) -> str:
        return self.config.tools[0].name if self.config.tools else "look"

    async def _run_delegation(self, call: VoiceEvent) -> None:
        spec = self.config.tools[0] if self.config.tools else None
        t0 = time.perf_counter()
        did = call.call_id

        async def say(text: str) -> None:
            """The answer as soon as it is written, before its tools have run, said once per spoken request (Said:
            marked only once its send returned). One that waits on steps (claims.Draft.pending) is said with those
            steps named as not done yet, so only what it says of them waits for their outcome, which the model judges
            (live 2026-10-28: "I've brought up the measurements table" before any tool had run)."""
            rid = self._request_of(did)
            if not text.strip() or self._conn is None or self.closed or f"answer:{rid}" in self.said:
                return
            pending = getattr(text, "pending", ())
            words = text.strip()[:1800] + (f" Steps still being done ({DOING}): {', '.join(pending)}." if pending else "")
            self.last_commentary_at = time.monotonic()
            try:
                await append_context(self._conn.session.commentary, words, delegation_id=did, staging=self._conn.session.thinking)
            except Exception:
                return
            self.said.said_answer(rid, text.strip())

        try:
            result = await spec.handler({**call.args, "say": say}) if spec else {"error": "no tool to handle it"}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        ms = round((time.perf_counter() - t0) * 1000)
        self._tool_tasks.pop(call.call_id, None)
        if self._conn is None or self.closed:
            return
        if result.get("superseded"):
            # The user corrected this request since: its outcome stays in the chat and the records,
            # and is not spoken as the answer now.
            self._emit(VoiceEvent("tool_result", call_id=call.call_id, name=call.name, result=result, data={"ms": ms}))
            return
        try:
            rid = self._request_of(did, result)
            said = self.said.answer(rid)
            whole = split_findings(result, said=said, request_id=rid)
            body = " ".join(part for part in (whole.spoken, whole.facts) if part)
            stale = bool(result.get("stale"))
            if body and not stale and not only_in_progress(result):
                await self._hold_for_user()  # never over the user's words; what they said meanwhile changes nothing
                if self._conn is None or self.closed:
                    return  # Talk ended while it waited: not said, so the next session may say it
            # Computed after any wait: whatever said it meanwhile (the narrator, another hand-off) is left out.
            fresh = split_findings(result, said=said, request_id=rid, already=self.said)
            if body and stale:
                # The user has asked something newer since (brain/speech.py newest): the outcome is in the chat and
                # told to the voice as context, never said over the new question (ADR 0016's waits).
                await self._think("Context only, do not say now: the outcome of the user's earlier request, which they "
                                  "have since followed with a newer one.\n" + body, source="stale", delegation_id=did)
            elif body and only_in_progress(result):
                # Nothing has happened yet: the voice is told, never asked to say it (live 2026-10-11: "Still pulling
                # that photo. Still at it."; "It's still being done"). The chat's row shows it working; the outcome
                # is spoken when it lands.
                await self._think("Not to be said again: the user has been told this is being done; its outcome follows "
                                  "on this delegation.\n" + body, source="in-progress", delegation_id=did)
            elif fresh.spoken:
                if fresh.facts:
                    await self._think("For you only, not to be read out: " + fresh.facts, source="facts", delegation_id=did)
                self.last_commentary_at = time.monotonic()
                await append_context(self._conn.session.commentary, fresh.spoken, delegation_id=did, staging=self._conn.session.thinking)
                self.said.claim(fresh.outcomes)  # marked said only once its send returned (a failed send raises above)
            elif whole.spoken:
                # Every outcome here was said already (its late result, another hand-off of the same request, an earlier
                # Talk session): told, not said again (run 4 of the voice journeys; live 2026-10-28, said three times).
                await self._think("Context only, already said: " + body, source="said", delegation_id=did)
            elif body:
                await self._think("For you only, not to be read out: " + body, source="facts", delegation_id=did)
        except Exception as exc:
            self._emit(VoiceEvent("error", data={"message": f"delegation result failed: {type(exc).__name__}: {exc}"[:300]}))
        self._emit(VoiceEvent("tool_result", call_id=call.call_id, name=call.name, result=result, data={"ms": ms}))
