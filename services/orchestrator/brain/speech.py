"""Spoken requests: turn-taking between what the user says, GPT-Live and the brain.

GPT-Live hears and speaks; the brain sees and acts. For one voice session (one Talk press)
this decides:

- **joining**: a request can arrive in pieces ("Magnify" ... "that"); pieces closer than
  JOIN_PIECES_S are one request, one turn in the conversation (the chat shows them as one line that grows), and words the fast pass judges unfinished ("Can you close the") wait up to
  JOIN_UNFINISHED_S for the rest and are joined with it; a cancel ("never mind") calls off
  what was about to be done. The model judges both, not word lists (brain/intent.py);
- **one record per request**: each settled request is one ticket (ticket.py) carrying its words,
  their span on the voice's own clock, what was pointed at when it was heard and its own work
  (hands, the look, the turn). Nothing is matched by comparing words;
- **hands**: what can be done from notes (magnify, highlight, close, show a result, break
  down, start a researcher) is done as soon as the words are heard, without waiting for the
  voice to hand it over;
- **hand-offs**: GPT-Live's delegation carries no task, only its place on the voice's clock
  (`offset_ms`). It is matched to the request whose words were said before it, never to an older
  one, and the brain answers that request in its own words (Brain.turn), reusing what hands did.
  Other requests still in progress keep their own identity: they are context, never joined in;
- **corrections**: words the fast pass judges to correct the request before ("no, the other one")
  call off what that request has not finished; what it made stays;
- **pointing along**: when the voice answered a question itself and nothing was done, the app
  points at what it talked about; never at a remark;
- **where it is filed**: the user's words and the voice's reply go to the conversation they
  were said in (dialogue.py).

The clock is injected, so tests replay a session's timing without waiting for it.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, replace
from uuid import uuid4
from typing import Callable
from .. import execution

from ..contract import SPEECH_LINE_GAP_S
from .claims import hands_note
from .intent import ACTS, CANCEL, WAIT
from .narrator import Line
from .registry import CameraSelection, ToolContext, ToolFailed, ToolRun, actions_wire

JOIN_PIECES_S = 0.3  # pieces of one spoken request closer than this are joined before acting
JOIN_UNFINISHED_S = 4.0  # words the fast pass judged unfinished wait this long to be joined with the rest
WAIT_FOR_WORDS_S = 2.5  # a hand-off waits this long for the request it belongs to to be heard
WORDS_GRACE_S = 0.5  # ...or this long when it carries words (their final transcript is already on its way)
POINT_AFTER_S = 1.5  # wait this long for a hand-off before pointing on the voice's behalf
HANDS_KEPT_S = 15.0  # hands' held view stays reusable by the request's hand-off this long
LOOK_KEPT_S = 10.0  # a look started on heard words (W2-1) waits this long for its hand-off
HANDS_RESULT_S = 6.0  # a hand-off waits at most this long for hands to decide
HEARD_KEPT = 8  # requests kept for matching hand-offs to
RECENT_VOICE_S = 12.0  # without the voice's clock, a hand-off is matched only to a request heard this recently
SPAN_SLACK_MS = 250  # the voice's clock steps in 200 ms: a request starting this soon after the delegation is before it
AGAIN_MS = 2000  # a second delegation this soon after a request's words, with nothing newer heard, is that request again


@dataclass(frozen=True)
class Heard:
    """What was heard for one spoken request."""
    words: str
    at: float  # our clock, when its last piece was heard
    start_ms: float | None = None  # its span on the voice's own clock (ms from the session's start)
    end_ms: float | None = None
    when_asked: object = None  # board.WhenAsked as of its last piece: what "that" means
    pin: object = None  # the Studio's pin as of its last piece, or the error that made it unavailable
    conversation_turn: object = None  # timed Live transcript filed before its answer can arrive


def findings(brain, result: dict, request=None) -> dict:
    """A brain turn as the findings GPT-Live is given (voice/openai_live.split_findings), with the identity of the
    spoken request it answers (`requestId`, its ticket's admission), so what is said of it is said once."""
    scene = brain.hub.scene.objects
    ids = [i for i in result["objectIds"] if i in scene]
    found = {"answer": result["answer"], "outlined": [scene[i].label or "an unnamed outline" for i in ids],
             "outlinedIds": ids, "targets": result.get("targets", 0), "actions": result["actions"],
             "model": result["model"]}
    rid = _request_id(request)
    if rid:
        found["requestId"] = rid
    return found


def _request_id(request) -> str | None:
    admission = getattr(request, "admission", None)
    return getattr(admission, "request_id", None)


def outcome_line(found: dict, voice_owner, **how) -> Line:
    """The narrator's line for a spoken request's outcome the app reports without a hand-off (or before it): what the
    voice should say of it (split_findings' spoken part), with the identities of the request and of the outcomes it
    reports, so whichever path says it first says it once (narrator.Said)."""
    from ..voice.openai_live import split_findings

    split = split_findings(found)
    return Line(split.spoken, "requested", request_id=found.get("requestId"), outcomes=split.outcomes,
                voice_owner=voice_owner, **how)


REFILE_S = 2.0  # a line still growing is filed again whole at most this often: a crash keeps nearly all of it


class SpokenLine:
    """One side of the speech as one chat line: the pieces of a spoken request, or of a spoken reply, grow
    one line (live 2026-10-07 16:19: "the", "tools I", "have can't run ..." between "be able to do" and
    "this"; kind (j): one request, one line; one reply, one line).
    - **Filed where it began:** its first piece is filed at once (`Dialogue.show`), so a reload shows it before
      the answers and tool lines that came after it; its conversation is fixed then (the `Asked` rule), and
      every later filing goes to the same one.
    - **Growing:** it grows in the studios (`Dialogue.stream`) and is filed again whole at most every REFILE_S
      (a crash keeps nearly all of it), and when it closes; the same `lineId` replaces it in place (`SessionStore`).
    - **Closing:** when its side is done (a request settled, `SpokenRequests._settle`; the voice went quiet),
      when no piece has come for SPEECH_LINE_GAP_S, or when the session ends. Words judged unfinished
      take the line up again (`reopen`), so their rest is the same line."""

    def __init__(self, role: str, dialogue, join: str, asked: Callable, closed: Callable[[str, object], None],
                 peek: Callable | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        # `asked` is consumed when the line closes; `peek` gives the conversation it is filed in from its first piece
        self.role, self.dialogue, self.join, self.asked_of, self.closed = role, dialogue, join, asked, closed
        self.peek, self.clock = peek or asked, clock
        self.id, self.text, self.asked = "", "", None
        self.filed_at = 0.0
        self.caption = False
        self.updated = None  # caption checkpoint: same conversation turn as its saved line
        self.span_end = None
        self.started_at = None
        self.speech_session = None
        self.last: tuple[str, str, object] | None = None  # the line closed last (id, text, asked), for `reopen`
        self.timer: asyncio.TimerHandle | None = None

    def _here(self):
        with contextlib.suppress(Exception):
            return self.dialogue.here() if self.dialogue is not None else None
        return None

    def add(self, piece: str, *, span: dict | None = None) -> None:
        # Live captions use the provider timeline, never packet arrival or audio silence.
        start = (span or {}).get("startMs")
        if self.caption and isinstance(start, (int, float)) and self.span_end is not None and start - self.span_end > 1200:
            self.close()
        first = not self.text
        if first and span:
            self.started_at = span.get("at")
            self.speech_session = span.get("speechSession")
        end = (span or {}).get("endMs")
        if isinstance(end, (int, float)):
            self.span_end = end
        if first:
            self.id, self.asked = f"v{time.time_ns():x}", (self.peek() or self._here())
        self.text = (self.text + self.join + piece) if self.text else piece
        if self.dialogue is not None:
            now = self.clock()
            if first or now - self.filed_at >= REFILE_S:
                self.checkpoint()
            else:
                self.dialogue.stream(self._line(), asked=self.asked)
        if self.timer is not None:
            self.timer.cancel()
        with contextlib.suppress(RuntimeError):
            # Persist a quiet suffix without inventing a provider turn boundary.
            self.timer = asyncio.get_running_loop().call_later(REFILE_S if self.caption else SPEECH_LINE_GAP_S,
                                                               self.checkpoint if self.caption else self.close)

    def checkpoint(self) -> None:
        if not self.text:
            return
        if self.dialogue is not None:
            self.dialogue.show({**self._line(), "open": True}, asked=self.asked)
        self.filed_at = self.clock()
        if self.caption and self.updated is not None:
            self.updated(self.id, self.text.strip(), self.asked)

    def reopen(self) -> None:
        """The line closed last is taken up again (words judged unfinished: their rest is the same request)."""
        if self.text or self.last is None:
            return
        self.id, self.text, self.asked = self.last
        self.last = None
        self.filed_at = 0.0

    def _line(self) -> dict:
        return {"type": "chat", "role": self.role, "text": self.text.strip(), "voice": True,
                "echo": not self.caption, "lineId": self.id,
                **({"at": self.started_at, "speechSession": self.speech_session} if self.started_at is not None else {})}

    def close(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        if self.caption:
            self.checkpoint()
        consumed = self.asked_of() if self.text else None
        # Filed where it began: the conversation fixed at its first piece, the same line (its lineId) made whole.
        text, asked = self.text.strip(), (self.asked if self.asked is not None else consumed)
        line = self._line()
        if text:
            self.last = (self.id, self.text, asked)
        self.id, self.text, self.asked = "", "", None
        self.span_end = self.started_at = self.speech_session = None
        if not text:
            return
        if self.dialogue is not None:
            self.dialogue.show(line, asked=asked)
        self.closed(text, asked)


class SpokenRequests:
    def __init__(self, brain, dialogue=None, voice=None, clock: Callable[[], float] = time.monotonic, *, client=None) -> None:
        self.brain = brain
        self.dialogue = dialogue  # where the user's words and the voice's reply are shown (dialogue.py)
        self.voice = voice  # the GPT-Live session: told silently what hands did
        self.client = client  # exact accepted Studio socket; never the newest connected browser
        self.clock = clock
        self.requests: list = []  # the requests heard, oldest first (ticket.Ticket with .heard), the last HEARD_KEPT
        self.words = ""  # the request being heard, pieces joined so far
        self.span: tuple[float | None, float | None] = (None, None)  # its span on the voice's clock
        self.pending: asyncio.Task | None = None  # waiting to see if more pieces come
        self.asked = None  # the conversation the user's last request went to: the reply goes there too
        self.handed_at = 0.0  # when the voice last handed something over
        self.cancelled_at = float("-inf")  # when the fast pass last judged words a cancel ("never mind")
        self.carry = None  # (when, the request judged unfinished): its words wait for the rest
        self._handoffs = {}  # delegation id -> its answer, in flight or done: a replay never runs it again
        # The chat lines of the two sides, each one line that grows (see SpokenLine).
        self.user_line = SpokenLine("user", dialogue, " ", lambda: self.user_line.asked, lambda text, asked: None,
                                   peek=lambda: self.brain.conversation.here(), clock=clock)
        self.reply_line = SpokenLine("assistant", dialogue, "", self._reply_asked, self._reply_filed, peek=lambda: self.asked,
                                    clock=clock)
        self._caption_turn = None
        self.reply_line.updated = self._caption_reply
        if voice is not None:
            with contextlib.suppress(Exception):
                voice.spoken = self  # what the narrator asks before speaking a late outcome (newest)
                voice.requests = self  # which request a delegation is (voice/openai_live)

    def newest(self, request_id: str | None) -> bool:
        """Whether the request (by its admission id) is still the user's latest: no later words heard since that are
        more than filler, unfinished or a cancel. A late outcome of an older request is not said over a newer one
        (ADR 0016's waits); unknown requests count as newest, so nothing is withheld on a guess."""
        mine = next((r for r in self.requests if r.admission is not None and r.admission.request_id == request_id), None)
        if mine is None:
            return True
        later = [r for r in self.requests if r.heard.at > mine.heard.at and r is not mine and r.superseded_by is None]
        return not any(r.intent not in (WAIT, CANCEL, "filler") for r in later)

    def _closed(self) -> bool:
        return self.voice is None or self.voice.closed

    def request_for(self, delegation_id: str | None) -> str | None:
        """The spoken request a delegation was matched to (its admission's id), None when none was: the identity the
        voice says each request's answer and outcome once by (voice/openai_live)."""
        if not delegation_id:
            return None
        return next((_request_id(r) for r in reversed(self.requests) if delegation_id in r.delegations), None)

    @property
    def _hands_requests(self) -> dict:
        """Hands' task -> its request, read from the records (a view, never a second store)."""
        return {r.work["hands"]: r for r in self.requests if r.work.get("hands") is not None}

    # --- what the user says and what the voice says ----------------------------------------
    def heard(self, piece: str, span: tuple | None = None) -> None:
        """A piece of what the user said (a final input transcript), with its span on the voice's clock."""
        if getattr(self.brain, "narrator", None) is not None and hasattr(self.brain.narrator, "user_spoke"):
            self.brain.narrator.user_spoke()  # waiting results may be covered by what is said next (W2-5)
        conversation = self.brain.conversation
        if hasattr(conversation, "was_heard"):
            conversation.was_heard(piece)  # the user's own words, for the checks of what they explicitly asked
        start, end = (span or (None, None))[:2]
        start = start if isinstance(start, (int, float)) else None
        end = end if isinstance(end, (int, float)) else None
        carried = self.carry if self.pending is None and self.carry is not None \
            and self.clock() - self.carry[0] < JOIN_UNFINISHED_S else None
        # One line for the request, to every open studio as it grows and kept whole for a reload; "echo":
        # the talking studio already shows it live from its own socket and skips it.
        if carried is not None and not self.user_line.caption:
            self.user_line.reopen()  # the rest of words judged unfinished: the same request, the same line
        if not self.user_line.caption:
            self.user_line.add(piece.strip())
        if self.pending is not None:
            self.words = (self.words + " " + piece).strip()
            self.span = (self.span[0] if self.span[0] is not None else start, end if end is not None else self.span[1])
        elif carried is not None:
            unfinished = carried[1]
            self.words = (unfinished.heard.words + " " + piece).strip()  # the rest of what was cut off
            self.span = (unfinished.heard.start_ms if unfinished.heard.start_ms is not None else start, end)
        else:
            self.words, self.span = piece, (start, end)
        self.carry = None
        wait = JOIN_PIECES_S
        if self.pending is not None:
            self.pending.cancel()
        # A request is made when its last piece is heard (the final transcript): a click while the pieces are
        # joined is after it. What was pointed at then, and the Studio's pin then, go with it.
        pin = None
        if self.client is not None:
            try:
                pin = self.brain.hub.screen.pin(self.client, request_id=str(uuid4()), timing='server-final-transcript')
            except Exception as error:
                pin = error  # unavailable at the time of asking must not acquire a newer window
        turn = None
        if self.user_line.caption:
            turn = conversation.add_once("user", "voice", self.words, asked=self.user_line.asked)
            self.asked = conversation.asked_of(turn)
        heard = Heard(self.words, self.clock(), self.span[0], self.span[1], self._pointing_now(), pin, turn)
        self.pending = asyncio.ensure_future(self._settle(heard, wait, carried[1] if carried is not None else None))

    def handed_over(self) -> None:
        """The voice handed something to the app (its ask_app tool was called)."""
        self.handed_at = self.clock()

    def said(self, piece: str, *, span: dict | None = None) -> None:
        """A piece of what the voice is saying: one line that grows until its turn is done."""
        self.reply_line.add(piece, span=span)

    def reply_done(self) -> None:
        """The voice's turn is done (it went quiet): its line is filed whole, with the request it answers, and
        what it says next is a new line. The user's line is the request's: it closes when the request settles."""
        if self.reply_line.caption:
            self.reply_line.checkpoint()
        else:
            self.reply_line.close()

    def close(self) -> None:
        """The session ended: whatever is still being said is filed."""
        self.user_line.close()
        self.reply_line.close()

    def replied(self, text: str) -> None:
        """What the voice said in reply, whole."""
        self.reply_line.add(text)
        self.reply_done()

    def _reply_asked(self):
        asked, self.asked = self.asked, None  # used once: a later reply goes to the conversation open then
        return asked

    def _caption_reply(self, identity: str, text: str, asked) -> None:
        current = self._caption_turn
        if current is not None and current[0] == identity:
            if current[1].text != text:
                turn = current[1]
                origin = self.brain.conversation.asked_of(turn)
                self.brain.conversation.store.elsewhere(origin.session).edit_turn(turn, text)
        else:
            self._caption_turn = (identity, self.brain.conversation.add("assistant", "voice", text, asked=asked,
                                                                      voiceLineId=identity))

    def _reply_filed(self, text: str, asked) -> None:
        """The reply is a turn of the conversation it answers."""
        if not self.reply_line.caption:
            self.brain.conversation.add("assistant", "voice", text, asked=asked)

    async def _settle(self, heard: Heard, wait: float, continues=None) -> None:
        await asyncio.sleep(wait)
        self.pending = None
        conversation = self.brain.conversation
        # One turn per spoken request, its pieces joined (the chat still shows each piece).
        self.asked = conversation.asked_of(heard.conversation_turn or conversation.add_once("user", "voice", heard.words))
        if not self.user_line.caption:
            self.user_line.close()  # legacy transcripts; timed captions own their grouping
        request = self._request(heard)
        if continues is not None:
            continues.superseded_by = request  # its words were unfinished: this request is its whole
        await self._act_or_point(request)

    def _request(self, heard: Heard):
        """The record of one spoken request: a hands ticket carrying what was heard."""
        from ..ticket import issue

        request = issue(self.brain.hub, "hands", asked=self.brain.conversation.here(), channel="voice")
        request.heard = heard
        self.requests.append(request)
        del self.requests[:-HEARD_KEPT]
        return request

    def _pointing_now(self):
        """What is pointed at now (board.WhenAsked), or None without a board (tests)."""
        board = getattr(self.brain, "board", None)
        try:
            return board.when_asked() if board is not None else None
        except Exception:
            return None

    def _file_steps(self, request, runs, turn_id, asked) -> None:
        """A spoken request's steps, into the chat as a typed request's are filed (commands.ask: chat-action rows):
        the studio shows each step working, its card and its late result in place, the same by voice as typed (voice/
        chat parity, live 2026-10-11: by voice no step, card or "Photo displayed" showed). Each step once, by its own
        identity: a hand-off that reuses what hands did files nothing again."""
        if self.dialogue is None or request is None:
            return
        filed = request.work.setdefault("filed", set())
        for run in runs or []:
            wire = run.wire() if hasattr(run, "wire") else dict(run)
            key = wire.get("actionId") or (wire.get("tool"), wire.get("summary"))
            if key in filed or not wire.get("tool"):
                continue
            filed.add(key)
            with contextlib.suppress(Exception):
                self.dialogue.show({"type": "chat-action", "turnId": turn_id, **wire, "standalone": True}, asked=asked)

    def _file_turn(self, request, turn) -> None:
        if isinstance(turn, dict):
            self._file_steps(request, turn.get("actions"), turn.get("turnId"), turn.get("asked"))
            # With no voice to say it (Talk ended while it was answered), the answer is the chat's, as a typed one is
            # (live 2026-10-11 16:36:22: the steps' rows reached the chat, the answer went only to an ended voice).
            answer = str(turn.get("answer") or "").strip()
            if answer and self._closed() and self.dialogue is not None and not request.work.get("answer_filed"):
                request.work["answer_filed"] = True
                with contextlib.suppress(Exception):
                    self.dialogue.show({"type": "chat", "role": "assistant", "text": answer, "turnId": turn.get("turnId"),
                                        "actions": turn.get("actions") or []}, asked=turn.get("asked"))

    async def _act_or_point(self, request) -> None:
        """Hands: do what can be done from notes as soon as the words are heard. If nothing was
        done and the voice does not hand the request over, point at what it talked about (a
        highlight is part of the answer)."""
        text, heard_at = request.heard.words, request.heard.at
        voice_owner = self.voice
        result = None
        # The pointing reflex (option REFLEX "lean..."): the thing a question is about is outlined while the voice
        # answers, at once and whether or not it hands the request over (ADR 0016's Act loop); hands acts beside it.
        lean = str(getattr(self.brain.hub.settings, "reflex", "after-voice")).startswith("lean")
        if lean and request.intent not in (WAIT, CANCEL):
            request.work["reflex"] = asyncio.ensure_future(self.point(request))
        try:
            result = await self.hands(request)
        except Exception as error:
            if self.client is not None:
                self.brain.agent.log({'kind': 'voice-action-failed', 'question': text, 'error': str(error)[:400]})
                if voice_owner is not None and not voice_owner.closed:
                    with contextlib.suppress(Exception):
                        await voice_owner.inject('The spoken request encountered an execution error: ' + str(error)[:400]
                                                 + '. Completion is unconfirmed. Explain the failure briefly; do not infer that the connection is broken or that the capability is unavailable.', respond=True)
                return
        brain = self.brain
        if result is not None and result.get("runs"):
            ctx = result.get("ctx")
            self._file_steps(request, result["runs"], getattr(ctx, "turn_id", None), getattr(ctx, "asked", None))
        if request.intent in (WAIT, CANCEL):
            return  # unfinished (waiting for the rest) or called off: nothing to do or point at
        if result is not None and result.get("plan_failed"):
            # A failed fast provider call is not an answered request. Continue the
            # same request through the brain; a late delegation reuses this task.
            await asyncio.sleep(max(0.0, POINT_AFTER_S - (self.clock() - heard_at)))
            if request.delegations or self._closed():
                return
            await self._do_unhanded(request, result)
            return
        if result is not None and result.get("answered"):
            result = None  # answered from notes, nothing done: point along, as before
        if result is not None and not result["runs"] and not result.get("later"):
            # Hands only started a look for words that need one (W2-1): the hand-off uses it.
            # A request ("zoom in on the plant cell") is done even when the voice answers it
            # itself and never hands it over (live demo 2026-10-06 00:33: "Sure, I'll bring it
            # closer", and nothing happened): the app does it with this look and says what
            # happened. A question without a hand-off is pointed along with, as before.
            decision = result["decision"]
            if getattr(decision, "intent", "") == "request":
                await asyncio.sleep(max(0.0, POINT_AFTER_S - (self.clock() - heard_at)))
                if request.delegations or self._closed():
                    return
                await self._do_unhanded(request, result)
                return
            if lean and getattr(decision, "intent", "") == "question" and result.get("look") is not None:
                # A question about something the eyes' notes do not hold, answered by the voice without a hand-off:
                # the look hands started is not dropped (the demo's look-unused); it finishes, outlines, and the voice
                # adds or corrects only if it differs (ADR 0016: sync, then async; voice journeys run 5).
                await asyncio.sleep(max(0.0, POINT_AFTER_S - (self.clock() - heard_at)))
                if request.delegations or self._closed():
                    return
                await self._look_closer(request, result)
                return
            result = None
        if result is not None:
            # The voice is told what is done, what is still being done and what did not work
            # (brain/claims.py), never only what worked (2026-10-04 14:06:56: told "cleared 2
            # outlines", it said "Done" while the highlight had missed).
            note = hands_note(result["runs"], result.get("later") or [], brain.registry.kind,
                              getattr(result["decision"], "view", None),
                              needs_look=bool(getattr(result["decision"], "needs_look", False)))
            if note and self.voice is not None:
                with contextlib.suppress(Exception):  # silently
                    await self.voice.inject(note, respond=False, source="hands")
            rest = result.get("later") or getattr(result["decision"], "needs_look", False)
            if not any(r.ok is False for r in result["runs"]) and not rest:
                return
            # Something did not work, or part of it needs a new look. A hand-off of this request
            # looks (Brain.turn) and its findings say what happened; without one, look here and
            # say the outcome, so a miss is never left as a silent "done". What hands did is
            # not done again.
            await asyncio.sleep(max(0.0, POINT_AFTER_S - (self.clock() - heard_at)))
            if request.delegations or self._closed():
                return
            # Recorded before it starts, so a hand-off of this request that arrives now waits for
            # it instead of looking and outlining a second time (speed bench, live-152750).
            task = asyncio.ensure_future(brain.turn(text, "voice", handled=result, when_asked=request.heard.when_asked,
                                                   voice_owner=voice_owner))
            request.work["turn"] = task
            try:
                turn = await task
            except Exception as error:
                await self._execution_problem(text, result, recovering=False, error=error)
                return
            self._file_turn(request, turn)
            if brain.narrator is not None and voice_owner is not None:
                brain.narrator.offer(outcome_line(findings(brain, turn, request), voice_owner))
            return
        if lean:
            return  # the reflex pointed already, beside hands
        await asyncio.sleep(max(0.0, POINT_AFTER_S - (self.clock() - heard_at)))
        if request.delegations or self._closed():
            return
        with contextlib.suppress(Exception):
            await self.point(request)

    async def _do_unhanded(self, request, result: dict) -> None:
        """A spoken request the voice answered itself without handing it over: the app does it
        (with hands' look) and says what happened; a failure is said at once, since the voice has
        already promised it. A hand-off of this request that comes later gets this result instead
        of acting again."""
        voice_owner = self.voice
        brain = self.brain
        task = asyncio.ensure_future(brain.turn(request.heard.words, "voice", handled=result,
                                               when_asked=request.heard.when_asked, voice_owner=voice_owner))
        request.work["turn"] = task
        try:
            turn = await task
        except Exception as error:
            await self._execution_problem(request.heard.words, result, recovering=False, error=error)
            return
        self._file_turn(request, turn)
        if brain.narrator is not None and voice_owner is not None:
            found = findings(brain, turn, request)
            failed = any(a.get("ok") is False for a in found["actions"])
            brain.narrator.offer(outcome_line(found, voice_owner, delivery="interrupt" if failed else "when_idle"))

    async def _look_closer(self, request, result: dict) -> None:
        """Finish the closer look for a question the voice answered alone: its outlines are drawn, and the voice is
        given what it found beside what it already said, to say only what adds to or corrects it (the model judges;
        nothing compares words). A hand-off of this request that comes later reuses this turn."""
        from ..untrusted import inline

        voice_owner, brain, text = self.voice, self.brain, request.heard.words
        task = asyncio.ensure_future(brain.turn(text, "voice", handled=result, when_asked=request.heard.when_asked,
                                               voice_owner=voice_owner))
        request.work["turn"] = task
        try:
            turn = await task
        except Exception as error:
            await self._execution_problem(text, result, recovering=False, error=error)
            return
        self._file_turn(request, turn)
        if brain.narrator is None or voice_owner is None:
            return
        from ..voice.openai_live import findings_text

        found = findings_text(findings(brain, turn, request))
        if not found:
            return
        said = (self.reply_line.text or (self.reply_line.last or ("", "", None))[1] or "").strip()
        request_id = request.admission.request_id if request.admission is not None else None
        from ..options import REFLEX_MODELS
        from .fast import adds

        model = REFLEX_MODELS.get(getattr(brain.hub.settings, "reflex", "")) or brain.hub.settings.fast
        try:
            more, say = await adds(text, said, found, brain.agent.vision(model), model)
        except Exception:
            more, say = False, ""
        brain.agent.log({"kind": "look-closer", "question": text, "adds": more, "say": say, "model": model})
        if more:
            brain.narrator.offer(Line(f"Tell the user briefly, in your own words, what your closer look added: {inline(say, 300)}",
                                      "requested", provenance="model", voice_owner=voice_owner, request_id=request_id,
                                      outcomes=frozenset({f"closer:{request_id}"}) if request_id else frozenset(),
                                      still=lambda: self.newest(request_id)))
        else:  # nothing new: told, never said
            brain.narrator.offer(Line(f'Your closer look at "{inline(text, 200)}" found: {inline(found, 900)}',
                                      "requested", delivery="silent", voice_owner=voice_owner))

    async def _execution_problem(self, text: str, result: dict, *, recovering: bool, error=None) -> None:
        """Persist a real execution failure and give its facts to the original voice model."""
        ctx = result["ctx"]
        confirmed = [r.summary for r in result.get("runs", []) if r.ok is True]
        summary = ("The fast planner failed to return a decision; recovery is running. No action is confirmed yet."
                   if recovering else
                   "The spoken request could not finish. Completion of the remaining work is unconfirmed.")
        if confirmed:
            summary += " Earlier confirmed results: " + "; ".join(confirmed)
        facts = {"question": text, "status": "recovering" if recovering else "failed",
                 "confirmedResults": confirmed, "errorType": type(error).__name__ if error else None}
        if self.dialogue is not None:
            self.dialogue.notice(producer={"kind": "voice-execution"},
                                 event="planning-recovery" if recovering else "request-failed",
                                 summary=summary, facts=facts, asked=ctx.asked,
                                 origin={"requestId": ctx.request_id, "turnId": ctx.turn_id}, tone="error")
        self.brain.agent.log({"kind": "voice-execution-failure", "requestId": ctx.request_id, **facts})
        self.brain.conversation.add("note", "voice", summary, asked=ctx.asked)
        voice = ctx.voice_owner
        if voice is not None and not voice.closed:
            with contextlib.suppress(Exception):
                await voice.inject(summary + " Original request: " + text +
                                   (" Do not claim success while recovery is pending." if recovering else
                                    " Explain this failure naturally and correct any earlier claim of completion."),
                                   respond=not recovering, source="recovery")

    # --- hands: acting on spoken words as they are heard -------------------------------------
    def hands(self, request) -> asyncio.Task:
        """Do now what these words ask that can be done from notes; the request's hand-off picks up this
        result instead of acting twice. `request` is the request's record (or, for a caller with words
        only, its words: a record is made for them)."""
        if isinstance(request, str):
            pin = None
            if self.client is not None:
                try:
                    pin = self.brain.hub.screen.pin(self.client, request_id=str(uuid4()), timing='server-handoff-arrival')
                except Exception as error:
                    pin = error
            request = self._request(Heard(request, self.clock(), when_asked=self._pointing_now(), pin=pin))
        try:
            origin = self._hold_camera(request)
        except BaseException:
            request.seal()
            raise
        task = asyncio.ensure_future(self._hands_run(request.heard.words, request, origin))
        request.work["hands"], request.work["origin"] = task, origin
        request.track(task)  # pending Hands itself owns its held view through settlement
        return task

    def _hold_camera(self, request) -> dict:
        """Select once before Hands yields; absence is as fixed as a real held Capture."""
        actual_closed = request.on_close

        def retire(closed):
            try:
                # A closed request keeps its identity and judgment, not its held camera or context.
                for role in ("origin", "hands"):
                    closed.work.pop(role, None)
                look = closed.work.pop("look", None)
                if look is not None:
                    look.drop()
            finally:
                if actual_closed is not None:
                    actual_closed(closed)

        request.on_close = retire
        hub = self.brain.hub
        source = getattr(hub, "source", None)
        info = getattr(hub, "latest_info", None)
        fresh = getattr(source, "is_fresh", None)
        offline = getattr(source, "bridge_offline", None)
        capture = (request.store.get(info.id) if info is not None and fresh is not None and fresh()
                   and (offline is None or not offline()) else None)
        ctx = ToolContext("voice", 0, info.id if info else None, request.heard.words, when_asked=request.heard.when_asked,
                          asked=request.asked, admission=request.admission, request_id=request.admission.request_id,
                          camera_selection=CameraSelection(None, capture), voice_owner=self.voice)
        if self.client is not None:
            ctx.client = self.client
            ctx.request_snapshot = self._workspace_pin(request, request.admission.request_id)
            ctx.workspace_id = ctx.request_snapshot.observation.client.workspace_id
        selector = getattr(getattr(self.brain, "sight", None), "select", None)
        if callable(selector):
            ctx.sight_selection = selector(request)
        origin = {"ticket": request, "ctx": ctx}
        try:
            if capture is not None:
                request.hold(capture, "analysis", "bytes")
        except ToolFailed as error:
            # Retain the actual reservation refusal for both Hands and its handoff.
            # An unheld Capture cannot become a later successful camera selection.
            origin["camera_error"] = error
            return origin
        origin["camera_deadline"] = asyncio.get_running_loop().time() + HANDS_KEPT_S
        keep = asyncio.create_task(self._keep_camera(request, origin["camera_deadline"]))
        request.track(keep)
        origin["camera_keep"] = keep
        return origin

    def _workspace_pin(self, request, request_id):
        """The Studio's pin as of when the request was heard, bound to this request's admission."""
        screen = self.brain.hub.screen
        pin = request.heard.pin
        if pin is None:
            return screen.pin(self.client, request_id=request_id, timing='server-handoff-arrival')
        if isinstance(pin, Exception):
            raise pin
        client, profile = screen._origin(pin)
        if client is not self.client:
            raise ToolFailed('the original spoken workspace is no longer connected', next='reconnect and ask again')
        return replace(pin, snapshot_id=screen._snapshot_id(profile, request_id, pin.observation, pin.timing))

    async def _keep_camera(self, request, deadline: float) -> None:
        """Ticket-owned, bounded handoff grace; delivery closure does not revoke it."""
        while not request.closed:
            remaining = deadline - asyncio.get_running_loop().time()
            if request.superseded("session") or remaining <= 0:
                return  # Ticket._task_done closes only after all tracked work settles
            await asyncio.sleep(min(0.1, remaining))

    @execution.traced("hands", request="voice")
    async def _hands_run(self, text: str, request=None, origin=None) -> dict | None:
        from ..ticket import using

        if request is None:  # words only (a direct caller): their own record and held view
            request = self._request(Heard(text, self.clock(), when_asked=self._pointing_now()))
            origin = self._hold_camera(request)
            request.work["origin"] = origin
        with using(request):
            try:
                if origin.get("camera_error") is not None:
                    raise origin["camera_error"]
                result = await self._hands(text, request, origin["ctx"], origin)
                if isinstance(result, dict):
                    # A hand-off of this request adopts this ticket's choices (Brain.turn), which stay
                    # open as long as a hand-off may come.
                    result.update(origin)
                    request.keep_choices(HANDS_KEPT_S)
                return result
            except BaseException:
                if origin.get("camera_keep") is not None:
                    origin["camera_keep"].cancel()
                raise
            finally:
                if request.superseded("session") or request.intent in (WAIT, CANCEL):
                    if origin.get("camera_keep") is not None:
                        origin["camera_keep"].cancel()
                request.seal()

    def _corrected(self, request) -> None:
        """These words correct the request before them (the fast pass's judgment): what it has not finished
        is called off; what it made stays, by its own identity."""
        earlier = next((r for r in reversed(self.requests) if r is not request and r.superseded_by is None
                        and r.heard.at <= request.heard.at and (r.intent is None or r.intent in ACTS)), None)
        if earlier is None:
            return
        earlier.superseded_by = request
        earlier.cancel_request("corrected by the user")
        look = earlier.work.get("look")
        if look is not None:
            look.drop()
        self.brain.agent.log({"kind": "voice-correction", "request": request.id, "corrects": earlier.id,
                              "question": request.heard.words, "earlier": earlier.heard.words})

    async def _hands(self, text: str, request, ctx: ToolContext, origin: dict) -> dict | None:
        started = self.clock()
        brain = self.brain
        settings = brain.hub.settings
        if brain.services is None or settings.fast == "off":
            return None
        base = {"kind": "hands", "question": text, "request": request.id, **settings.to_wire()}
        asked = ctx.when_asked
        scoped = ({'moment': brain.moment(asked, request_snapshot=ctx.request_snapshot)}
                  if ctx.request_snapshot is not None else {})
        quick = await brain.quick(text, when_asked=asked, **scoped)
        request.intent = quick.intent if quick is not None else None
        why = request.superseded("session")
        if why:
            raise ToolFailed(why, next="ask again in the current conversation")
        if request.closed:
            raise ToolFailed("the original spoken camera selection expired", next="ask again on the view now")
        keep = origin.get("camera_keep")
        if (keep is not None and not keep.cancelling()
                and asyncio.get_running_loop().time() >= origin["camera_deadline"]):
            raise ToolFailed("the original spoken camera selection expired", next="ask again on the view now")
        if origin.get("handoff_claimed"):
            # The timed-out handoff owns the remaining plan. This entered send
            # stays spent and held until settlement, but cannot act beside it.
            return None
        ctx.seen_view = quick.view if quick is not None else None
        # Legacy outlines still act on the newest frame. Camera acquisition uses only
        # the immutable selection retained before the fast call, including None.
        info = brain.hub.latest_info
        ctx.frame_id = info.id if info else None
        if quick is not None and quick.intent == WAIT:
            # Cut off mid-sentence (the model's judgment): joined with what comes next.
            self.carry = (self.clock(), request)
            brain.agent.log({**base, "actions": [], "intent": WAIT, "why": "unfinished: waiting for the rest",
                             "ms": round(quick.ms)})
            return None
        if quick is not None and quick.intent == CANCEL:
            # Called off (the model's judgment): what was about to be done is not done, and what
            # earlier spoken requests are still doing changes nothing on screen when it lands
            # (their tickets are cancelled; ticket.py).
            from ..ticket import cancel_open

            self.cancelled_at = started
            cancel_open("voice", request.created, "the user called it off")
            brain.agent.log({**base, "actions": [], "intent": CANCEL, "why": "called off", "ms": round(quick.ms)})
            return None
        if quick is not None and getattr(quick, "relation", "new") == "correction":
            request.relation = "correction"
            self._corrected(request)
        if quick is not None and quick.needs_look and quick.actions:
            # Part of it needs a look, part does not ("close the magnifier and highlight the
            # stomata", the stomata not in the notes): the steps the fast pass gave are done now,
            # and the look continues from them (`preceding`); the close does not wait 3-5 s for the
            # vision call. One owner: a look already started for this request was planned before
            # these steps, so it is replaced by one that knows them.
            now, later = brain.split_for_look(quick.actions)
            runs = []
            if self.cancelled_at < started:
                origin["effects_claimed"] = True
                runs = await brain.run_actions(now, ctx)
            look = self._start_look(request, preceding=runs)
            brain.agent.log({**base, "actions": [r.wire(full=True) for r in runs], "intent": quick.intent,
                             "ms": round(quick.ms), "needsLook": True, "later": actions_wire(later),
                             "lookStarted": look is not None})
            return {"decision": quick, "runs": runs, "ctx": ctx, "later": later, "look": look}
        if quick is None or quick.needs_look or not quick.actions:
            # The voice answers it, or hands it over for a look. Logged too, so "why did
            # nothing happen?" can be answered from the log. A look it will need starts now
            # (W2-1): the hand-off reuses this decision and this look.
            look = self._start_look(request) if quick is not None and quick.needs_look else None
            brain.agent.log({**base, "actions": [], "needsLook": bool(quick and quick.needs_look),
                             "intent": quick.intent if quick else "", "ms": round(quick.ms) if quick else None,
                             "lookStarted": look is not None})
            if quick is not None and quick.needs_look:
                # Even with speculation off, the handoff reuses this decision and
                # spends its next planning slot on the required look, not another fast pass.
                return {"decision": quick, "runs": [], "ctx": ctx, "later": [], "look": look}
            if quick is not None and not quick.needs_look:
                # Answered from notes, nothing to do: a hand-off of this request uses this
                # decision instead of asking the fast pass again (2026-10-06: 40 of 47
                # hand-offs did, about 2 s each).
                return {"decision": quick, "runs": [], "ctx": ctx, "later": [], "look": None, "answered": True}
            failed = {"decision": None, "runs": [], "ctx": ctx, "later": [], "look": None, "plan_failed": True}
            await self._execution_problem(text, failed, recovering=True)
            return failed
        # A highlight of what the look saw before the slide moved waits for a new look; the
        # rest is done now ("close the zoom" does not wait for "highlight the blue part").
        now, later = brain.split_for_look(quick.actions)
        if self.cancelled_at >= started:
            brain.agent.log({**base, "actions": [], "why": "called off (\"never mind\")", "ms": round(quick.ms)})
            return None
        if not now:
            brain.agent.log({**base, "actions": [], "needsLook": True, "why": "the look is not current", "ms": round(quick.ms)})
            quick.needs_look = True  # deferred actions require the picture, regardless of the notes-only guess
            return {"decision": quick, "runs": [], "ctx": ctx, "later": later, "look": self._start_look(request)}
        origin["effects_claimed"] = True  # synchronous claim before the runner can enter any effect
        runs = await brain.run_actions(now, ctx)
        look = self._start_look(request, preceding=runs) if later else None
        brain.agent.log({**base, "actions": [r.wire(full=True) for r in runs], "intent": quick.intent, "ms": round(quick.ms),
                         "needsLook": bool(later), "later": actions_wire(later),
                         "lookStarted": look is not None})
        # Returned even when it all failed: the hand-off then looks again (Brain.turn), and the
        # voice is told what did not work, instead of nothing happening. `later`: what still
        # needs this request's look.
        return {"decision": quick, "runs": runs, "ctx": ctx, "later": later, "look": look}

    def _start_look(self, request, *, preceding=()):
        """The vision call for this request, started before the voice hands it over (W2-1; option
        SPECULATE), on the request's own words. One look per request: a look started before steps
        that changed the board is replaced by one that continues from them. Dropped, and logged, if
        no hand-off picks it up within LOOK_KEPT_S."""
        brain = self.brain
        if getattr(brain.hub.settings, "speculate", "off") == "off" or not hasattr(brain, "start_look"):
            return None
        existing = request.work.get("look")
        if existing is not None and existing.usable():
            if not preceding:
                return existing
            existing.drop_for_request()  # planned before these steps ran: its board is gone
        try:
            ctx = (request.work.get("origin") or {}).get("ctx")
            scoped = {'request_snapshot': ctx.request_snapshot} if ctx is not None and ctx.request_snapshot is not None else {}
            if preceding:
                scoped["preceding"] = tuple(preceding)
            look = brain.start_look(request.heard.words, when_asked=request.heard.when_asked, **scoped)
        except Exception:
            return None
        request.work["look"] = look

        def expire() -> None:
            if look.drop():
                with contextlib.suppress(Exception):
                    brain.agent.log({"kind": "look-unused", "question": request.heard.words,
                                     "why": "no hand-off of this request picked it up"})

        asyncio.get_running_loop().call_later(LOOK_KEPT_S, expire)
        return look

    def _adopt(self, runs: list[ToolRun], ctx: ToolContext) -> None:
        """Researchers hands started before the hand-off arrived belong to that hand-off, so
        their results are spoken on the delegation that asked (they were left with none)."""
        manager = self.brain.services.research if self.brain.services is not None else None
        if manager is None:
            return
        for run in runs:
            if run.tool != "start_researcher" or not run.ok:
                continue
            # The researcher it started, by the identity its receipt carries (never read back from its words).
            for output in getattr(run, "outputs", None) or ():
                ref = output.get("resource") if output.get("kind") == "resource" else None
                rid = ref.get("researcherId") if isinstance(ref, dict) and ref.get("kind") == "research-run" else None
                session = manager.get(rid) if rid else None
                if session is not None and not (session.origin or {}).get("delegation_id"):
                    session.origin = {**(session.origin or {}), "delegation_id": ctx.delegation_id, "turn_id": ctx.turn_id}

    # --- the voice hands a request over --------------------------------------------------------
    def _state(self, request) -> str:
        """Whether a request can take a hand-off: "acts", "skip" (filler, a cancel, superseded) or
        "unfinished" (its rest is coming). Words the fast pass is still judging are the words the voice
        reacted to: they take it at once, so the look can start beside the fast pass."""
        if request.superseded_by is not None or request.intent in ("filler", CANCEL):
            return "skip"
        if request.intent == WAIT:
            return "unfinished"
        return "acts"

    def _resolve(self, offset_ms, received: float):
        """The request a delegation belongs to, and why: (request, how) or (None, why not yet).

        With the voice's clock (`offset_ms` and the requests' spans, both its own): the newest request said
        before the delegation. Without it: the newest request heard since the last one matched, and recently.
        Never an older request than the words the delegation came after; a request already matched to a
        delegation is matched again only when no newer words come (the voice handing it over twice)."""
        if self.pending is not None and not self.pending.done():
            return None, "joining"  # the newest words are still being joined: they come first
        pool = list(self.requests)
        # The voice's clock decides only when the newest words carry it: a request without a span is never
        # skipped over for an older one that has one.
        timed = isinstance(offset_ms, (int, float)) and bool(pool) and pool[-1].heard.start_ms is not None
        if timed:
            before = [r for r in pool if r.heard.start_ms is not None and r.heard.start_ms <= offset_ms + SPAN_SLACK_MS]
        else:
            bound = [r.heard.at for r in pool if r.delegations]
            last = max(bound) if bound else float("-inf")
            before = [r for r in pool if r.heard.at > last and received - r.heard.at <= RECENT_VOICE_S]
        for request in reversed(before):
            state = self._state(request)
            if state == "skip":
                continue
            if state == "unfinished":
                return None, state
            if request.delegations:
                return None, ("bound", request)
            return request, "offset" if timed else "clock"
        return None, "none"

    async def _bind(self, offset_ms, received: float, words: str = ""):
        """Wait for the request this delegation belongs to (its words may still be settling), bounded: the
        final transcript reaches the app just before the delegation, so with words carried the wait is short,
        unless words are still being joined."""
        while True:
            request, how = self._resolve(offset_ms, received)
            if request is not None:
                return request, how
            waiting = how[0] if isinstance(how, tuple) else how
            settling = waiting in ("joining", "unfinished") or not words.strip()
            if self.clock() >= received + (WAIT_FOR_WORDS_S if settling else WORDS_GRACE_S):
                if isinstance(how, tuple) and self._just_after(how[1], offset_ms):
                    return how[1], "again"  # no newer words came: the voice handed the same request over again
                return None, waiting
            await asyncio.sleep(0.05)

    @staticmethod
    def _just_after(request, offset_ms) -> bool:
        """The delegation came right after this request's words, on the voice's own clock."""
        said = request.heard.end_ms if request.heard.end_ms is not None else request.heard.start_ms
        return isinstance(offset_ms, (int, float)) and said is not None and 0 <= offset_ms - said <= AGAIN_MS

    async def handoff(self, words: str, delegation_id: str | None = None, say=None, *, offset_ms=None) -> dict:
        """Replay of a delegation awaits its original work, including its original budget."""
        if not delegation_id:
            return await self._handoff(words, delegation_id, say, offset_ms)
        task = self._handoffs.get(delegation_id)
        if task is None:
            task = asyncio.create_task(self._handoff(words, delegation_id, say, offset_ms))
            self._handoffs[delegation_id] = task
        return await asyncio.shield(task)  # stopping delivery does not cancel delegated work

    async def _handoff(self, words: str, delegation_id: str | None = None, say=None, offset_ms=None) -> dict:
        """GPT-Live handed a request to the app (its ask_app tool): the brain answers the request it belongs
        to, in that request's own words, reusing what hands already did; the findings go back for the voice
        to say. `words` (everything said since the voice's last hand-off) only names the request when nothing
        was heard at all since then: they are then all new."""
        voice_owner = self.voice
        received = self.clock()
        request, how = await self._bind(offset_ms, received, words or "")
        # Retiring Hands releases its camera/context, not its session identity.
        # A late delegation cannot turn that missing work into a new request in
        # the session now open, even after the recent-voice binding window.
        original = request or (self.requests[-1] if self.requests else None)
        why = original.superseded("session") if original is not None else None
        if why:
            raise ToolFailed(why, next="ask again in the current conversation")
        if request is None and (words or "").strip() and not any(
                r.heard.at > max((q.heard.at for q in self.requests if q.delegations), default=float("-inf"))
                for r in self.requests):
            # Nothing was heard since the last hand-off: the words it carries are all new, and the turn
            # answers them with its own fast pass.
            request, how = self._request(Heard(words.strip(), self.clock(), when_asked=self._pointing_now())), "words"
            request.seal()
        bound_at = self.clock()
        if request is None:
            # Nothing heard is this delegation's: nothing new is started, and the voice is told what is
            # still in progress, as facts.
            doing = [r.heard.words for r in self.requests if r.work.get("turn") is not None and not r.work["turn"].done()]
            self.brain.agent.log({"kind": "voice-handoff-unmatched", "id": delegation_id, "offsetMs": offset_ms,
                                  "why": how, "inProgress": doing})
            return {"answer": "", "outlined": [], "actions": [], "model": "", "unmatched": True, "inProgress": doing}
        request.delegations.append(delegation_id or "")
        superseded = request.superseded_by is not None
        turn = request.work.get("turn")
        if turn is None:
            turn = asyncio.ensure_future(self._answer(request, delegation_id, say, voice_owner))
            request.work["turn"] = turn
        try:
            result = await asyncio.shield(turn)
        except Exception:
            if request.superseded_by is None:
                raise
            # Corrected while it was being answered: called off, and not spoken as the answer now.
            return {"answer": "", "outlined": [], "actions": [], "model": "", "superseded": True}
        # Where a hand-off's time went before the brain started (make voice-report): matching it to its
        # request (wordsWaitMs), then hands (handsWaitMs, in the request's own answer facts).
        self.brain.agent.log({"kind": "voice-handoff-wait", "id": delegation_id, "request": request.id,
                              "matchedBy": how, "offsetMs": offset_ms, "spanMs": [request.heard.start_ms, request.heard.end_ms],
                              "hadWords": bool((words or "").strip()), "wordsWaitMs": round((bound_at - received) * 1000),
                              **request.work.get("answered", {"handsUsed": False, "reused": None, "earlyLook": False,
                                                              "handsWaitMs": 0}),
                              "turnMs": round((self.clock() - bound_at) * 1000)})
        found = findings(self.brain, result, request)
        if superseded or request.superseded_by is not None:
            found["superseded"] = True  # corrected since: its outcome is not spoken as the current answer
        elif request.admission is not None and not self.newest(request.admission.request_id):
            found["stale"] = True  # the user has asked something newer: told to the voice, not said over them
        return found

    async def _answer(self, request, delegation_id, say, voice_owner) -> dict:
        """The brain's answer to a request handed over, reusing what hands did with it."""
        from ..ticket import using

        why = request.superseded("session")
        if why:
            # Asked in the session left. Its hold ends within a tick of the session opening and retires its Hands
            # work, so the turn's own check of the carried ticket may never run: refused here, never answered on
            # the session open now with a fresh view and budget (flaky until 2026-10-13, by which came first).
            raise ToolFailed(why, next="ask again in the current conversation")
        began = self.clock()
        deciding = request.work.get("hands")
        retained = request.work.get("origin")
        early = None
        if deciding is not None and not deciding.done() and not (retained or {}).get("effects_claimed"):
            # Hands is still deciding (the fast pass): the look starts now beside it, as for a
            # typed question, instead of after it (2 s later); unused, it is dropped.
            with using(request):
                early = self._start_look(request)
        handled = None
        if deciding is not None:
            with contextlib.suppress(Exception):
                handled = await asyncio.wait_for(asyncio.shield(deciding), timeout=HANDS_RESULT_S)
            if handled is None and retained is not None:
                if retained.get("effects_claimed"):
                    # A claimed owner remains authoritative even when already
                    # done and failed. Its error cannot authorize an effect replay.
                    handled = await asyncio.shield(deciding)
                elif not deciding.done():
                    # Transfer before Brain.turn can await or dispatch. Do not
                    # close Hands: turn first acquires its original Capture lease.
                    retained["handoff_claimed"] = True
        if early is not None:
            look = request.work.get("look")
            if handled is None:
                # Hands decided nothing (no fast pass, or it was cut off): the look answers.
                handled = {"decision": None, "runs": [], "ctx": None, "later": [], "look": look if look is not None else early}
            elif handled.get("look") is None and look is early:
                early.drop_for_request()  # answered from notes or done from them: no look needed
        if deciding is not None:
            if handled is None:
                handled = {"decision": None, "runs": [], "ctx": None, "later": [], "look": None}
            handled["ticket"] = request
            if retained is not None:
                handled.update(retained)
        request.work["answered"] = {
            "handsUsed": handled is not None, "earlyLook": early is not None, "handsWaitMs": round((self.clock() - began) * 1000),
            "reused": (("look" if handled.get("look") is not None else "answer" if handled.get("answered") else "actions")
                       if handled is not None else None)}
        done = (handled or {}).get("ctx")
        asked = getattr(done, "when_asked", None) or request.heard.when_asked
        # Always the newest frame: the look and its outlines are on what is in view now (2026-10-06: a frame
        # from when the user began to speak was 10-17 s old, and swimming cells had moved).
        draft = say
        if request.intent == "request" and say is not None and voice_owner is not None:
            # A request to act was acknowledged by the voice already; the answer written before its steps run says
            # what is being done, which the chat's rows show: told, not said, and the outcome is said when it lands
            # (the user's 2026-10-11 decision: "Sure", the card, then "There it is"). A question's answer is said.
            async def draft(text: str) -> None:
                with contextlib.suppress(Exception):
                    await voice_owner.inject("Context only, do not say: being done for the user's request (its outcome "
                                             "follows on this delegation): " + text, respond=False, source="draft")
        result = await self.brain.turn(request.heard.words, "voice", delegation_id=delegation_id, handled=handled,
                                       draft=draft,  # a question's answer is said as soon as it is written
                                       when_asked=asked, voice_owner=voice_owner)
        if handled is not None and delegation_id and handled.get("runs"):
            self._adopt(handled["runs"], handled["ctx"])
        self._file_turn(request, result)
        return result

    async def point(self, request, channel: str = "voice") -> list[str]:
        from ..ticket import issue, using

        if isinstance(request, str):
            request = next((r for r in reversed(self.requests) if r.heard.words == request), None) or \
                self._request(Heard(request, self.clock(), when_asked=self._pointing_now()))
        ticket = issue(self.brain.hub, "hands", asked=self.brain.conversation.here(), channel=channel)
        ticket.adopt_choices(request)
        with using(ticket):
            try:
                return await self._point(request, channel)
            finally:
                ticket.seal()

    async def _point(self, request, channel: str = "voice") -> list[str]:
        """The voice answered from its notes without handing over: point at what it talked about
        anyway (highlight what was seen, select what is outlined). Returns new outlines."""
        text = request.heard.words
        voice_owner = self.voice if channel == "voice" else None
        brain = self.brain
        settings = brain.hub.settings
        if brain.services is None or settings.fast == "off":
            return []
        if not brain.sight_current():
            # The slide moved since the eyes last looked: their ids no longer say where things are.
            brain.agent.log({"kind": "point", "channel": channel, "question": text, "actions": [],
                             "skipped": "the look is from before the slide moved", **settings.to_wire()})
            return []
        if settings.user_leads:
            return []  # the user finds things themselves: nothing is pointed at unasked
        asked = request.heard.when_asked
        from ..ticket import current
        ticket = current()
        pin = self._workspace_pin(request, ticket.admission.request_id) if self.client is not None else None
        scoped = {'moment': brain.moment(asked, request_snapshot=pin)} if pin is not None else {}
        lean = str(getattr(settings, "reflex", "after-voice")).startswith("lean")
        if lean:
            quick = await brain.reflex(text, scoped.get("moment"), when_asked=asked)
        else:
            quick = await brain.quick(text, point_only=True, when_asked=asked, **scoped)
        if quick is None or quick.intent != "question":
            return []  # the voice points along only with an answer to a question (the model's judgment)
        if not quick.actions:
            brain.agent.log({"kind": "point", "channel": channel, "question": text, "actions": [], "intent": quick.intent,
                             "ms": round(quick.ms), **settings.to_wire()})  # settings carry the reflex option
            return []
        info = brain.hub.latest_info
        ctx = ToolContext(channel, 0, info.id if info else None, text, None, [], when_asked=asked, seen_view=quick.view,
                          asked=brain.conversation.here(), voice_owner=voice_owner)
        if pin is not None:
            ctx.client, ctx.request_snapshot = self.client, pin
            ctx.workspace_id = pin.observation.client.workspace_id
            ctx.request_id, ctx.admission = ticket.admission.request_id, ticket.admission
        runs = await brain.run_actions(quick.actions, ctx)
        brain.agent.log({"kind": "point", "channel": channel, "question": text, "actions": [r.wire(full=True) for r in runs],
                         "intent": quick.intent, "ms": round(quick.ms), **settings.to_wire()})
        # What the pointing put on screen is told to the voice, so it may say it; pointing nobody asked for that
        # did not work is never told (ADR 0016's Act loop: it is in the log and Activity, not in the words).
        done = [r for r in runs if r.ok]
        note = hands_note(done, [], brain.registry.kind)
        if note and voice_owner is not None and not voice_owner.closed:
            with contextlib.suppress(Exception):
                await voice_owner.inject(note, respond=False)
        self._file_steps(request, done, None, getattr(ctx, "asked", None))  # in the chat as the request's steps
        return list(ctx.objects)
