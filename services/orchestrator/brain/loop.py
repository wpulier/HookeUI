"""The brain: one mind for chat and voice.

`Brain.turn` answers a request from either channel. The fast path is one model call that
returns the answer, the targets to outline and any tool actions (json-actions mode, the
same proven answer prompt with a tools section). Effect tools then run concurrently; each is
waited for up to its `inline_ms` so the reply can mention it, and anything slower finishes
in the background and is reported when done. Only query tools cause another model call
(at most MAX_CALLS in total).

Memory is the shared Conversation's last few turns plus a compact table of what is on
screen: nothing is stored or retrieved beyond that, so a turn costs what it did before.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import time
from dataclasses import dataclass, field, replace
from typing import Awaitable, Callable

from .. import activity, tools, execution, scientific_ask
from ..capture import Capture, Refused
from ..options import VISION
from . import context
from .context import Moment
from .actions import CLEARING, ActionRunner, first_choices
from .intent import log_override
from .registry import CameraSelection, Registry, Steps, ToolContext, ToolFailed, ToolRun, action_number, actions_wire, dependent, parse_actions
from .state import Conversation
from .admission import AdmissionDenied, MAX_CALLS
from .dispatch import PLANNER_OUTPUT_TOKENS, Refused as CallRefused, answer_phase, capture_answer_attempts, dispatch_answer

MAX_TARGETS = 15
# Tools that take the user to another conversation or session: the request and its answer are filed where they end up.
MOVES = ("switch_thread", "switch_session", "new_project", "new_session")
PLANNER_TIMEOUT_S = 15.0  # bounded room for complete plans; entered cancellations stay spent
# Why this task's last quick decision did not come, when the network refused it (a ContextVar: concurrent requests
# never read each other's). The no-picture path says it instead of "did not answer".
from contextvars import ContextVar as _ContextVar

_QUICK_REFUSED: _ContextVar = _ContextVar("quick_refused", default=None)
REFLEX_TIMEOUT_S = 3.0  # a pointing reflex later than this would land after the voice's answer: no point
REWRITE_TOKENS = 600  # an answer written again from receipts; 300 cut some off (2026-10-11 review)


# What an answer names when it tells the user about each problem (p1...) and each model choice (c1...): the reply's
# `stated` (ADR 0006, the plan's Slice 4). One wording for the completion round and the answer written from the
# receipts (_reconcile), so the two can't drift (journeys round 2, 2026-10-11).
CHOICES_HEADING = "Model choices in this request, each said with what it applies to:"
STATED = 'stated: the ids under "What did not work" and "Model choices" that your answer tells the user about.'


def stated_ids(runs, n_targets: int, n_outlined: int, outline_errors, fast: bool, unshown=(), answer: str = "",
               also=()) -> dict[str, str]:
    """The ids an answer states: every problem (a step not settled, nothing outlined of the targets, an outline not
    drawn, a model `answer` names that no receipt names) and every model choice with what it applies to."""
    from .claims import choice_scopes, model_problems, unresolved

    # A step tried again successfully is settled, not a failure to tell (claims.unresolved).
    problems = [f"{r.tool}: {r.summary}" for r in unresolved(runs) if r.summary]
    if n_targets and not n_outlined and not fast and not unshown:
        problems.append("outline: " + (outline_errors[0] if outline_errors else "nothing there could be outlined"))
    # Outlined but not drawn (its place on the live view unverified): never said to be shown.
    problems += [f"outline: {why}" for why in unshown]
    # An answer names only models its receipts name (T4, live 2026-10-11 16:28:55: "with the DINO model", no step used
    # one); `also`: what else the request's answer was given (its notes, the models of its own looks and drawings).
    # Only for a request whose steps ran: a question answered from the notes may name the models it is asked about.
    problems += model_problems(answer, runs, also) if answer and runs else []
    ids = {f"p{n}": p for n, p in enumerate(problems, 1)}
    # A model choice is written from its receipt too, said with what it applies to: live 2026-10-11 every redraw
    # after "use SAM 2.1" was answered "switched to SAM 2.1", the receipt saying "for this request".
    ids.update({f"c{n}": f'{c["said"]} (applies: {c["applies"]})' for n, c in enumerate(choice_scopes(runs), 1)})
    return ids


def id_lines(ids: dict[str, str], kind: str) -> str:
    return "\n".join(f"- {i}: {text}" for i, text in ids.items() if i.startswith(kind))


def stated_block(ids: dict[str, str]) -> str:
    """The completion round's part of the contract: the same ids, the same wording, so its answer can stand without
    another call when it states them all (journeys round 2: one more call on every turn that chose a model)."""
    parts = []
    if id_lines(ids, "p"):
        parts.append("What did not work:\n" + id_lines(ids, "p"))
    if id_lines(ids, "c"):
        parts.append(CHOICES_HEADING + "\n" + id_lines(ids, "c"))
    return "\n".join(parts) + "\nWith your answer, include in the JSON " + STATED


def without_ids(text: str, ids: dict[str, str]) -> str:
    """The answer without the app's own ids for its problems and choices ("(p1)", "[c2]"): they go in `stated`, never in
    the words the user reads (live 2026-10-11: "...did not need to run again (p1)."). Only the ids it was given."""
    if not text or not ids:
        return text
    import re

    keys = "|".join(re.escape(i) for i in ids)
    text = re.sub(rf"\s*[(\[]\s*(?:{keys})(?:\s*[,;/]\s*(?:{keys}))*\s*[)\]]", "", text)
    return re.sub(r"\s+([.,;:!?])", r"\1", text).strip()


# The shapes of the app's ids in words: outlines and parts (o12, p2), things a look saw (s2), cells (c926), the index's
# entries (d8, e3, m1, r2, k1, i4, g1, b3) and native windows (w-3019b540-...). A word of this shape is taken out only
# when it is one of the app's ids now (`is_id`), so "(p53)" or "(G1 phase)" in the words is never touched.
APP_ID = r"(?:[opscdemrkigb]\d{1,6}|w-[0-9a-f]{8}[0-9a-f-]*)"


def without_app_ids(text: str, is_id: Callable[[str], bool]) -> str:
    """The answer without the app's ids in parentheses: "(o1)", "(d8, 2026-10-11)", "(outline o12)" go, "(s2, lower
    centre)" keeps "(lower centre)" (live 2026-10-11: 16 of 67 answers showed ids). Formatting only: ids in the running
    text are left to the answer prompts, as removing them there would break the sentence. Only words of the app's id
    shapes (APP_ID) that are its ids now (`is_id`: the board, the look, the index), never a word list; a parenthesis
    with none is left exactly as it was, and of one with an id only the pieces beside it ("(c926, largest)") stay."""
    if not text or "(" not in text:
        return text
    import re

    token = re.compile(rf"(?<![\w-]){APP_ID}(?![\w-])")

    def fix(match) -> str:
        pieces = [p for p in re.split(r"\s*[,;/]\s*", match.group(1)) if p.strip()]
        kept = [p for p in pieces if not any(is_id(m.group(0)) for m in token.finditer(p))]
        if len(kept) == len(pieces):
            return match.group(0)
        # a piece naming an id goes whole ("outline o12"); the pieces beside it stay
        return f" ({', '.join(kept)})" if kept else ""

    return re.sub(r"[ \t]*\(([^()]*)\)", fix, text)  # the space before a parenthesis goes with it; nothing else moves


def states_all(ids: dict[str, str], stated) -> bool:
    """Whether a reply's `stated` names every id it was given."""
    return isinstance(stated, list) and all(i in stated for i in ids)


def results_followup(question: str, results: str) -> str:
    """The same action-authoring contract for inline and completed late results."""
    return (f"{question}\n\n(Results of every step already attempted in this request:\n"
            f"{results}\n"
            "These are attempted steps, not a plan to replay. Add actions for work the results do not yet fulfill; "
            "repeat a step only when its result gives a reason for a deliberate retry. "
            "A failed step is not undone or retried merely because another query returned.\n"
            "A successful step may still concern the wrong source; check it against this request. "
            "If no step ran, use the available tools for the requested work or state the concrete blocker. "
            "Return actions for any further work you choose within this request, or an answer if finished. "
            "Only returned actions will run; describing an action does not execute it.)")


@dataclass
class Look:
    """A vision call started before its request is confirmed (hands, brain/speech.py; W2-1):
    the spoken words needed a look, so it starts at once instead of when the voice hands the
    request over. Input only: nothing is outlined, run or said from it until a hand-off of the
    same words picks it up in Brain.turn; unused, it is dropped (and logged)."""
    words: str
    view: tuple  # (info, image, crop): the frame it looks at
    moment: Moment
    tool_text: tuple
    task: asyncio.Task
    started: float  # time.perf_counter() when it started
    used: bool = False
    request_id: str = ""
    attempt_ids: list[str] = field(default_factory=list)
    log: Callable | None = field(default=None, repr=False)
    admission: object = field(default=None, repr=False)  # the request's (refunded once when its request goes on without it)
    discarded_outcome: dict | None = field(default=None, init=False)
    forgiven: bool = field(default=False, init=False)  # refunded to its request when dropped (admission.forgive)
    _dropped: bool = field(default=False, init=False)

    def usable(self) -> bool:
        """Not used yet (one look answers one turn: hands' own retry and the hand-off must not
        both point from it), not dropped, not failed."""
        return (not self.used and not self._dropped and not self.task.cancelled()
                and not (self.task.done() and self.task.exception() is not None))

    def drop(self) -> bool:
        """Cancel it if nothing picked it up; True if it was dropped unused."""
        if self.used:
            return False
        if not self._dropped:
            self._dropped = True
            if self.task.done():
                self._discard_finished(self.task)
            else:
                # Register before cancel: a provider may catch cancellation and fail.
                self.task.add_done_callback(self._discard_finished)
                self.task.cancel()
        return True

    def drop_for_request(self) -> bool:
        """Drop it unused while its request goes on (replaced by a look that knows steps run since, or not
        needed after all): it does not count against the request, once per request."""
        dropped = self.drop()
        if dropped and self.admission is not None and hasattr(self.admission, "forgive"):
            self.forgiven = bool(self.admission.forgive(self.attempt_ids))
        return dropped

    def _discard_finished(self, task) -> None:
        if self.discarded_outcome is not None:
            return
        error = None if task.cancelled() else task.exception()  # consume only the discarded look's exception
        status = "canceled" if task.cancelled() else "failed" if error is not None else "done"
        message = ""
        if error is not None:
            try:
                message = " ".join(str(error)[:200].split())
            except Exception:
                message = "failure message unavailable"
        self.discarded_outcome = {
            "kind": "look-discarded", "requestId": self.request_id,
            "attemptIds": list(self.attempt_ids[:MAX_CALLS]), "status": status,
            "stage": "answer-provider" if self.attempt_ids else "before-answer-provider",
            "error": f"{type(error).__name__}: {message}" if error is not None else "",
        }
        if self.log is not None:
            try:
                self.log({**self.discarded_outcome, "attemptIds": list(self.discarded_outcome["attemptIds"])})
            except Exception:
                pass  # the retained outcome and admission survive optional logging failure

# Delivers a tool result that finished after the turn's answer: (channel, text, delegation_id).
LateReporter = Callable[[ToolContext, ToolRun], Awaitable[None]]


def replaced_by(before: dict, new_objects: list, scene_objects: dict, max_px: float = 40.0) -> dict[str, str]:
    """Outlines a new answer replaced, mapped to the new outline of the same thing (nearest
    centre within max_px, in the current view), so the model's actions can still use the ids
    it saw."""
    aliases = {}
    for old_id, old in before.items():
        if old_id in scene_objects or not new_objects:
            continue
        ox, oy = old.center[0] - old.anchor_offset[0], old.center[1] - old.anchor_offset[1]
        best = min(new_objects, key=lambda n: (n.center[0] - n.anchor_offset[0] - ox) ** 2 + (n.center[1] - n.anchor_offset[1] - oy) ** 2)
        d = ((best.center[0] - best.anchor_offset[0] - ox) ** 2 + (best.center[1] - best.anchor_offset[1] - oy) ** 2) ** 0.5
        if d <= max_px:
            aliases[old_id] = best.id
    return aliases




def needs_another_look(runs: list[ToolRun]) -> bool:
    """Acting from notes did not work: nothing worked, or a highlight missed."""
    return bool(runs) and (all(r.ok is False for r in runs) or any(r.ok is False and r.tool == "highlight" for r in runs))


# An answer that says an action already happened ("Here it is outlined", "I've closed it").
SENTENCE = re.compile(r"(?<=[.!?])\s+")


def draft_text(answer: str) -> str:
    """Historical bench helper; never used to alter a production model response."""
    return " ".join(s for s in SENTENCE.split(answer.strip()) if s and not claims_done(s)).strip()
# A word used as an adjective is no claim ("Colouring the outlined cells" was cut as if it said "outlined":
# acceptance flow 9 on footage, 2026-10-07).
_NOT_AFTER = "".join(f"(?<!{d} )" for d in ("the", "these", "those", "your", "their", "its", "any", "every")) + "(?<!-)"
# Words that say the app did something on screen. The common ones ("filled with tannins", "fluid-filled",
# "kept warm", "the slide moved") are claims only after a speaker saying it ("I've", "we", "they're now"...):
# measured on 1,112 real answers (M1.5c re-check S2).
_SPEAKER = r"(?:\bi\b|\bi've\b|\bi have\b|\bwe\b|\bwe've\b|\bit's\b|\bit is\b|\bthey're\b|\bthey are\b|\bare\b|\bis\b|\bnow\b)\s+(?:\w+\s+)?"
CLAIMS_DONE = re.compile(r"\b(here (it is|they are)|there (it is|they are|you go|we go)|i've|i have|done|showing|"
                         + _NOT_AFTER + r"(?:outlined|highlighted|selected|zoomed|magnified|closed|opened|cleared|started|faded|hid|resized))\b"
                         r"|^\s*(?:filled|kept|moved|shown|hidden|coloured|colored)\b"
                         r"|" + _SPEAKER + r"(?<!-)(?:filled|kept|moved|shown|hidden|coloured|colored)\b(?!\s+with\b)", re.I)


# A sentence that says something is being done is no claim that it is done ("Keeping the five largest cells
# highlighted and fading the rest" was cut as a claim: acceptance flow 9 on footage). "Showing" is a claim.
CLAUSE = re.compile(r"(?<=[.!?;])\s+|\s+(?:and|but|so)\s+(?=(?:it|they|i|we)\b)", re.I)  # "...; it is outlined"
ONGOING = re.compile(r"^(?:(?:i'm|i am|we're|we are|now|okay|ok|sure|right)[,!]?\s+)?(?!showing\b)[a-z]+ing\b", re.I)


def claims_done(text: str) -> bool:
    """Historical benchmark heuristic, not response authority or a production router."""
    return any(s and CLAIMS_DONE.search(s) and not ONGOING.match(s.strip()) for s in CLAUSE.split(text or ""))


class Brain:
    def __init__(self, hub, agent, registry: Registry, conversation: Conversation) -> None:
        self.hub = hub
        self.agent = agent
        self.registry = registry
        self.conversation = conversation
        self.voice = None  # the open voice session (orchestrator/voice/route.py sets it)
        self.report_late: LateReporter | None = None
        self.services = None  # brain/toolbox.Services: what the tools act on (app.App.build sets it)
        self.narrator = None  # orchestrator/brain/narrator.Narrator, set by server.create_app
        self.on_answer: list = []  # called with each turn's result (enrich marks what was asked about)
        self.sight = None  # orchestrator/sight.Sight: the running look at the view
        self.presenter = None  # orchestrator/presenter.Presenter: the result viewer
        self.voice_notes: list[str] = []  # chat happenings the voice should know, sent with the next view note
        self.board = None  # board.Board: what is on screen (app.App.build sets it)
        from .narrator import Said

        self.said = Said()  # what the voice was asked to say, by identity: one per brain, so every Talk session shares it

    async def _look(self, question, view, *, phase="look", **kwargs):
        with answer_phase(phase):
            return await self.agent.look(question, view, **kwargs)

    async def _answer_model(self, model, images, prompt, *, phase, reservation=None, max_output_tokens=700):
        with answer_phase(phase, reservation):
            return await dispatch_answer(lambda: self.agent.vision(model).ask(images, prompt, max_output_tokens=max_output_tokens),
                                         model=model, effort=VISION[model].spec.partition("@")[2])

    def _interpretation_failed(self, ctx, phase, reason, attempts=(), error=None):
        """Optional original-call facts, never a reply or private provider text."""
        def identity(value):
            return value if isinstance(value, str) and len(value) <= 128 else None

        try:
            admission = getattr(ctx, "admission", None)
            asked = getattr(ctx, "asked", None)
            record = {"kind": "answer-interpretation-failed", "phase": phase, "reason": reason,
                      "turnId": getattr(ctx, "turn_id", None),
                      "requestId": identity(admission.request_id) if admission is not None else None,
                      "attemptId": identity(attempts[-1]) if attempts else None,
                      "calls": admission.used if admission is not None else None,
                      "day": identity(getattr(asked, "day", None)),
                      "thread": identity(getattr(asked, "thread", None))}
            if error is not None:
                record["errorType"] = type(error).__name__[:128]
            self.agent.log(record)
        except Exception:
            pass  # optional diagnostics cannot replace the real interpretation outcome

    # --- context (brain/context.py renders it) -------------------------------------------
    def _used(self, runs, made) -> dict:
        """What this request chose (`use`) and what really ran for it: the implementations its steps' executions ran
        and those that drew its outlines (an unvoted blind arm's never). The turn log's settings are only the defaults
        (live 2026-10-11: SAM 2.1 tiny drew the request's outline while the log said EdgeTAM)."""
        from ..operations import runner
        from ..ticket import current

        request = current()
        choices = {op: {k: c[k] for k in ("implementation", "policy", "origin", "used", "refused") if c.get(k) is not None}
                   for op, c in (getattr(request, "choices", None) or {}).items() if isinstance(c, dict)}
        ran: list[dict] = []

        def add(entry: dict) -> None:
            if entry.get("implementation") and entry not in ran:
                ran.append(entry)

        executions = runner(self.hub)
        for run in runs:
            for eid in getattr(run, "executions", ()) or ():
                done = executions.get(eid)
                if done is not None and done.trial is None:
                    add({"operation": done.operation, "implementation": done.executed, "policy": done.policy,
                         "origin": done.origin})
        for o in made:
            by = getattr(o, "made_by", None) or {}
            if getattr(o, "trial", None) is None:
                add({"operation": by.get("operation"), "implementation": by.get("implementation"),
                     "policy": by.get("policy"), "origin": by.get("origin")})
        # Every choice in order with the step that made it (a choice per step: "SAM 2.1, then micro-sam").
        steps = [dict(c) for c in (getattr(request, "choice_log", None) or []) if isinstance(c, dict)]
        return {"choices": choices, "ran": ran, **({"steps": steps} if steps else {})}

    def moment(self, when_asked=None, pushes: bool = False, request: str = "", request_snapshot=None) -> Moment:
        """The app as a request sees it now (brain/context.py); `when_asked` (board.WhenAsked): what was
        pointed at when the request was made. `pushes`: for the brain's own prompt (what options
        OUTCOME_LINES and SESSION_CONTEXT push; nothing while they are off)."""
        moment = context.capture(self, tuple(self.voice_notes), when_asked, pushes=pushes, request=request,
                                 request_snapshot=request_snapshot)
        from ..ticket import current
        capture = getattr(current(), 'scientific_input', None)
        if capture is not None:
            moment = replace(moment, outlined=(), outlined_plain=(), selected=None, seen='', view=None,
                             moving='', held='', stage=scientific_ask.notes(capture))
        return moment

    @property
    def day(self):
        """The day's store (day/store.py), or None (tests without one)."""
        return getattr(self.conversation, "store", None)

    def view_note(self) -> str:
        """What the voice should know it sees right now (silent context for GPT-Live)."""
        return context.view_note(self.moment())

    def is_app_id(self, word: str) -> bool:
        """One of the app's ids now: an outline or part on the board, a native window, a thing the running look saw,
        or an entry of the session's index (a lookup by id, a few ms; words that are no id are never looked up)."""
        if word in self.hub.scene.objects or word.startswith("w-"):
            return True
        view = getattr(self.sight, "view", None)
        if any(t.id == word for t in getattr(view, "things", None) or ()):
            return True
        resources = getattr(self.hub, "resources", None)
        try:
            return resources is not None and resources.get(word) is not None
        except Exception:  # an index that can't be read leaves the words as they are
            return False

    def voice_recap(self, *, request_snapshot=None) -> str:
        """Everything a new voice session should know at once; what happened while it was off is
        told once."""
        text = context.voice_recap(self.moment(request_snapshot=request_snapshot))
        self.voice_notes.clear()
        return text

    def day_digest(self) -> str:
        """The rest of the day in a few lines (day/context.py)."""
        return context.digest(self.day, self.services)

    # --- a turn -------------------------------------------------------------------------
    @execution.traced("brain", request="ask")
    async def turn(self, text: str, channel: str = "chat", frame_id: int | None = None,
                   delegation_id: str | None = None, asked=None, handled: dict | None = None,
                   draft=None, when_asked=None, *, client=None, request_snapshot=None, request_ticket=None,
                   voice_owner=Ellipsis, research_selection=None, input_capture=None, said: str | None = None) -> dict:
        """`asked` (day/model.Asked): the conversation the request was made in, when the caller
        fixed it (the chat's line was shown already); else where the request's turn is.
        `handled`: what hands already did with these spoken words (brain/speech.py).
        `draft`: async callable given the answer as soon as it is written, before its tools run
        (the voice says it at once; OpenAI's Live delegation guide: stream useful results).
        `when_asked` (board.WhenAsked): what the user was pointing at when they made the request (by voice,
        when the words' final transcript came); saved now when not given. "That" means what was pointed at
        then (board.py; the partner's Amendment 1, C2).

        `client`, `request_snapshot`, `request_ticket`: trusted caller's accepted socket,
        immutable Send pin and already issued turn Ticket; all three are validated together.
        They retain one original admission and are passed explicitly to context and tools.

        `said`: the words as the user typed them, when `text` also carries context only the model reads (a
        researcher's composer: which researcher and which of its versions). The conversation, Activity and the
        interaction log keep `said`; the model is asked `text`.

        `voice_owner`: the original local session, captured before the first await.
        Only an omitted direct voice ingress may select the current session once;
        explicit None or an absent carried association stays unavailable.

        The turn is one request with its own ticket (ticket.py): the views it looks at are held
        until its last step is done, and a result arriving after a clear, a cancel or a newer
        answer changes nothing on screen (ADR 0010)."""
        from ..ticket import Ticket, generations, issue, using
        from ..capture import captures, RUN
        from ..workspace_observation import RequestSnapshot, ObservationError

        original_context = (handled or {}).get("ctx")
        if channel != "voice" or voice_owner is None:
            voice_owner = None
        elif original_context is not None:
            voice_owner = getattr(original_context, "voice_owner", None)
        elif voice_owner is Ellipsis:
            voice_owner = self.voice
        if when_asked is None and self.board is not None:
            when_asked = self.board.when_asked()  # typed: the request is made now
        request = request_ticket if request_ticket is not None else issue(self.hub, "turn", asked=asked, channel=channel)
        if (not isinstance(request, Ticket) or request.kind != "turn" or request.closed or request.sealed
                or request.admission is None or request.generations is not generations(self.hub)
                or request.store is not captures(self.hub) or request.channel != channel
                or request.superseded("session") or (asked is not None and request.asked != asked)):
            raise ObservationError("INVALID_REPORT", "the original request Ticket is unavailable or belongs elsewhere")
        try:
            if input_capture is not None:
                if (channel != 'chat' or handled or frame_id is not None or request.held(input_capture.id) is not input_capture
                        or getattr(request, 'scientific_input', None) is not input_capture):
                    raise ToolFailed('The saved input is not held by this original request.', next='attach the saved input again')
                from ..sight import SightSelection
                request.sight_selection = SightSelection()
                when_asked = None
            carried_ticket = (handled or {}).get("ticket")
            if (request_ticket is not None and carried_ticket is not None
                    and carried_ticket.admission is not request.admission):
                raise ObservationError("INVALID_REPORT", "carried work belongs to another original admission")
            carried_context = (handled or {}).get("ctx")
            inherited_workspace = (channel == 'voice' and request_ticket is None and client is None
                                   and request_snapshot is None and getattr(carried_context, 'client', None) is not None)
            admission = request.admission
            if inherited_workspace:
                if (not isinstance(carried_ticket, Ticket) or carried_ticket.kind != 'hands'
                        or carried_ticket.closed or carried_ticket.superseded('session')
                        or carried_ticket.generations is not request.generations or carried_ticket.store is not request.store):
                    raise ObservationError('CLIENT_MISMATCH', 'the original spoken workspace request is unavailable')
                client, request_snapshot = carried_context.client, carried_context.request_snapshot
                admission = carried_ticket.admission
            if client is not None and carried_context is not None:
                carried_client = getattr(carried_context, "client", None)
                carried_pin = getattr(carried_context, "request_snapshot", None)
                if (carried_client is not client or carried_pin is not request_snapshot
                        or getattr(carried_context, "request_id", None) != admission.request_id
                        or getattr(carried_context, "admission", None) is not admission):
                    raise ObservationError("CLIENT_MISMATCH", "carried work belongs to another originating pin")
            if client is not None or request_snapshot is not None:
                if client is None or not isinstance(request_snapshot, RequestSnapshot) or (request_ticket is None and not inherited_workspace):
                    raise ObservationError("INVALID_REPORT", "workspace requests need their original socket, pin and Ticket")
                screen = self.hub.screen
                origin, profile = screen._origin(request_snapshot)
                expected = screen._snapshot_id(profile, admission.request_id,
                                               request_snapshot.observation, request_snapshot.timing)
                if (origin is not client or profile.app_run_id != RUN or request_snapshot.snapshot_id != expected):
                    raise ObservationError("CLIENT_MISMATCH", "request pin differs from the original socket/run/admission")
                carried_look = (handled or {}).get("look")
                if carried_look is not None and (carried_look.moment.request_snapshot is not request_snapshot
                                                or carried_look.request_id != admission.request_id):
                    # New notes cannot change inputs of an already-dispatched task. Refuse
                    # before consumption; its admitted sends remain spent on this request.
                    raise ObservationError("CLIENT_MISMATCH", "carried look was dispatched for another request pin")
        except BaseException:
            request.seal()
            raise
        # The hand-off of words hands already acted on is the same request: a model hands chose for it
        # (`use`) covers what this turn outlines (contract 7; scaffolding review, M1.4 item 1).
        request.adopt_choices((handled or {}).get("ticket"))
        with using(request):
            try:
                original_camera = getattr(carried_context, "camera_selection", None)
                if isinstance(carried_ticket, Ticket) and carried_ticket.kind == "hands":
                    # A spoken handoff inherits the original selection, not a new view.
                    # Acquire its own real lease before ending Hands' grace or snapshotting.
                    keep = (handled or {}).get("camera_keep")
                    if (carried_ticket.store is not request.store
                            or carried_ticket.generations is not request.generations
                            or carried_ticket.channel != channel):
                        raise ToolFailed("the original spoken camera selection belongs to another request",
                                         next="ask again on the view now in the current conversation")
                    why = carried_ticket.superseded("session")
                    if why:
                        if keep is not None:
                            keep.cancel()
                        raise ToolFailed(why, next="ask again in the current conversation")
                    if (handled or {}).get("camera_error") is not None:
                        raise handled["camera_error"]
                    if carried_ticket.closed:
                        raise ToolFailed("the original spoken camera selection is no longer held",
                                         next="ask again on the view now")
                    deadline = (handled or {}).get("camera_deadline")
                    if (deadline is not None and (keep is None or not keep.cancelling())
                            and asyncio.get_running_loop().time() >= deadline):
                        raise ToolFailed("the original spoken camera selection expired", next="ask again on the view now")
                    capture = original_camera.capture if original_camera is not None else None
                    if capture is not None:
                        from ..capture import Capture

                        if not isinstance(capture, Capture) or carried_ticket.held(capture.id) is not capture:
                            raise ToolFailed("the original spoken camera selection is no longer held",
                                             next="ask again on the view now")
                        request.hold(capture, "analysis", "bytes")
                    from ..sight import SightSelection

                    selection = (getattr(carried_context, "sight_selection", None)
                                 or getattr(carried_ticket, "sight_selection", None) or SightSelection())
                    sight_capture = selection.capture
                    if sight_capture is not None:
                        if carried_ticket.held(sight_capture.id) is not sight_capture:
                            raise ToolFailed("the original spoken running look is no longer held",
                                             next="ask again on the view now")
                        request.hold(sight_capture, "analysis", "bytes")
                    request.sight_selection = selection  # including original absence
                    if keep is not None:
                        keep.cancel()  # Ticket.track closes Hands once its other work is done
                elif input_capture is None and callable(getattr(self.sight, "select", None)):
                    self.sight.select(request)  # before snapshot, context refresh or provider await
                with activity.request_scope(self.hub):  # Activity: the request's entry, finished or failed
                    return await self._turn(text, channel, frame_id, delegation_id, asked, handled, draft, when_asked,
                                            client, request_snapshot, voice_owner, research_selection, input_capture, said)
            finally:
                request.seal()

    async def _turn(self, text: str, channel: str, frame_id: int | None, delegation_id: str | None, asked,
                    handled: dict | None, draft, when_asked, client=None, request_snapshot=None,
                    voice_owner=None, research_selection=None, input_capture=None, said=None) -> dict:
        from ..ticket import current

        settings = self.hub.settings
        budget = current().admission
        needs_final_room = (settings.fast != "off" or bool(settings.compare) or settings.closeup == "naming+answers"
                            or bool(handled and handled.get("ticket")))
        final_hold = budget.reserve_final() if needs_final_room and budget.used < MAX_CALLS else None
        prior_hands = bool(handled and handled.get("ticket"))
        original_context = (handled or {}).get("ctx")
        original_camera = getattr(original_context, "camera_selection", None)
        if handled and not handled.get("look") and not handled.get("decision") and not handled.get("runs"):
            handled = None  # ownership was adopted by turn(), even when hands produced no usable answer
        question = text.strip()
        if not question:
            raise tools.ToolError("ask needs a question", next="type a question")
        said = (said or "").strip() or question  # what the user typed: what is kept and shown of the request
        # A look hands already started on these words (W2-1) is used as it is: its frame, its
        # notes and its running vision call. One that failed or was dropped is not.
        look: Look | None = handled.get("look") if handled else None
        if look is not None and not look.usable():
            look = None
            if not handled.get("runs"):
                handled = None  # hands only started a look, and it is gone: answer afresh
        requested_camera_frame = frame_id  # keep the explicit id before Agent.snapshot's fallback
        view = (look.view if look is not None else None if (request_snapshot is not None or research_selection is not None)
                and not self.hub.source.is_fresh() else self.agent.snapshot(frame_id)) if input_capture is None else None
        if input_capture is not None:
            from ..marks import fov_crop
            image = await scientific_ask.decode(input_capture)
            view = (input_capture, image, fov_crop(None, image.shape[1], image.shape[0]))
        info, image, crop = view if view is not None else (None, None, None)
        picture_id = info.id if info is not None else None
        camera_selection = CameraSelection(None, None) if input_capture is not None else original_camera or CameraSelection(requested_camera_frame, info)
        from ..ticket import current

        if current() is not None and info is not None:
            current().hold(info, "analysis", "look")  # a look hands started is this turn's view too
        started = time.perf_counter()
        # Spoken words are also recorded from the transcript by the voice route; add_once
        # keeps one copy whichever arrives first.
        record_user = self.conversation.add if input_capture is not None else self.conversation.add_once
        user_turn = record_user("user", "voice" if channel == "voice" else channel, said, frame_id=picture_id,
                                asked=asked, **({'scientificInput': scientific_ask.facts(input_capture)} if input_capture is not None else {}))
        # Everything that answers it belongs where it was asked (a spoken request heard just
        # before a "new topic" opened another conversation stays in the one it was said in).
        asked_in = self.conversation.asked_of(user_turn)
        if current() is not None:
            current().asked = asked_in  # where what becomes of the request (an unused choice) is said
        execution.bind(turn_id=user_turn.id, thread=asked_in.thread, generation=asked_in.routing_generation,
                       day=self.day.sid if self.day is not None else None, words=said,
                       owner=request_snapshot.observation.client.wire() if request_snapshot is not None else None)
        activity.request_started(self.hub, user_turn.id, said, channel, asked_in)

        pointing = "box" if settings.pointing == "snap-first" else settings.pointing
        guess = tools.show_first_guess(self.hub, info.id, question, "ai") if input_capture is None and view is not None and settings.pointing == "snap-first" else None
        compare = None
        if input_capture is None and view is not None and channel == "chat" and settings.compare and settings.compare != settings.vision and self.agent.available(settings.compare):
            compare = asyncio.create_task(self._look(question, view, phase="compare", option=settings.compare, pointing=pointing))

        # What is on screen, captured once for this request: the vision call and the fast pass
        # decide on the same board.
        if look is None and settings.outcome_lines == "on":  # OUTCOME_LINES' read, off the event loop
            await asyncio.to_thread(context.make_outcome_lines, self.hub)
        moment = (look.moment if look is not None and look.moment.request_snapshot is request_snapshot
                  else self.moment(when_asked, pushes=True, request_snapshot=request_snapshot))
        table, selected = context.scene_table(moment)
        tool_text = (look.tool_text if look is not None and look.moment is moment
                     else (self.registry.prompt_section(), table, selected))
        from .claims import Draft, observation_limit

        draft_offered = False

        def offer_draft(answer, actions, runs=(), *, unavailable=False):
            nonlocal draft_offered
            query_dependent = (any(self.registry.kind(name) == "query" for name, _ in actions)
                               or any(self.registry.kind(run.tool) == "query" for run in runs))
            if (not draft_offered and draft is not None and answer and not unavailable
                    and not query_dependent and not any(run.ok is False for run in runs)):
                draft_offered = True
                # The steps it still waits on travel with it: with any, it is a plan, not an outcome (claims.Draft).
                pending = [name for name, _ in actions] + [run.tool for run in runs if run.ok is None]
                task = asyncio.create_task(draft(Draft(answer, pending)))
                self._drafts = {t for t in getattr(self, "_drafts", set()) if not t.done()} | {task}

        async def think(q: str, pointing_now: str, phase="look", *, preceding=()):
            planning_moment, planning_tools = moment, tool_text
            if preceding:
                # The fast pass may already have cleared or replaced outlines.
                # Continue from its receipts and current board, while retaining
                # the original Send ownership and what the user pointed at.
                q, planning_moment, planning_tools = self._continuation_inputs(
                    q, view, when_asked, request_snapshot, preceding)
            if view is None:
                _QUICK_REFUSED.set(None)
                decision = await self.quick(q, moment=planning_moment, followup=phase == "query" or bool(preceding), model=settings.vision)
                if decision is None:
                    refused = _QUICK_REFUSED.get()
                    if refused is not None:
                        raise refused  # the connection failed: said so, with what still works
                    raise tools.ToolError("the workspace notes planner did not answer", next="ask again")
                return ({"answer": decision.answer, "targets": [],
                         "actions": actions_wire(decision.actions),
                         **decision.observation_data(),
                         "goal": getattr(decision, "goal", None), "stated": getattr(decision, "stated", None)},
                        {"latency_ms": decision.ms, "model": decision.model, "fast": True})
            return await self._look(q, view, phase=phase, option=settings.vision, pointing=pointing_now, notes=planning_tools,
                                         mode=settings.brain, registry=self.registry)

        # Choose inputs before camera provider admission. An entered speculative look
        # cannot be refunded when the fast pass selects completed retained inputs.
        origin_context = {"client": client, "workspace_id": request_snapshot.observation.client.workspace_id
                          if request_snapshot is not None else None, "request_snapshot": request_snapshot,
                          "request_id": budget.request_id, "admission": budget,
                          "camera_selection": camera_selection, "voice_owner": voice_owner,
                          "sight_selection": moment.sight_selection, "research_selection": research_selection, "scientific_input": input_capture}
        if original_context is not None:
            origin_context["inspection_slots"] = original_context.inspection_slots
        pre_runs, pre_ctx = None, None
        carried, superseded, carried_ctx = [], [], None
        seen_view = moment.view
        vision_task, vision_at = None, None
        early: Look | None = None  # a typed request's look started beside the fast pass (EARLY_LOOK_TYPED)
        later: list[tuple[str, dict]] = []  # steps of the fast decision that need a new look
        if look is not None:
            # Hands judged these words and started the look (W2-1): the same decision, no second
            # fast pass, and the vision call is already running.
            look.used = True
            quick, later = handled["decision"], list(handled.get("later") or [])
            vision_task, vision_at = look.task, look.started
            if handled.get("runs"):
                pre_runs, pre_ctx = handled["runs"], handled["ctx"]
                pre_ctx.turn_id = pre_ctx.turn_id or user_turn.id
                if delegation_id and not pre_ctx.delegation_id:
                    pre_ctx.delegation_id = delegation_id
        elif handled is not None:
            # The app already acted on these spoken words as they were heard (hands,
            # brain/speech.py): no second look and no second action. The hand-off's delegation
            # and turn now own what hands did.
            quick, pre_runs, pre_ctx = handled["decision"], handled["runs"], handled["ctx"]
            later = list(handled.get("later") or [])
            if pre_ctx is None:  # hands answered from notes and had nothing to do: its answer stands
                pre_ctx = ToolContext(channel, user_turn.id, picture_id, question, delegation_id, [], when_asked=when_asked,
                                      seen_view=quick.view, asked=asked_in, **origin_context)
            pre_ctx.turn_id = pre_ctx.turn_id or user_turn.id
            if delegation_id and not pre_ctx.delegation_id:
                pre_ctx.delegation_id = delegation_id
        else:
            # A requested comparison occupies the second planning slot; keep the third for outcomes.
            # Explicit saved pixels go through the shared image-aware planner. The camera's
            # notes-only fast pass cannot substitute metadata for this accepted input.
            deciding = input_capture is None and settings.fast != "off" and not compare and not prior_hands and self.services is not None
            if deciding and view is not None and settings.early_look_typed == "alongside":
                # EARLY_LOOK_TYPED "alongside": the look starts beside the fast pass, as a spoken request's does. It
                # is used only when the fast pass needs it and gave no steps (or gave no decision); otherwise it is
                # dropped below before anything else is sent, and refunded to the request once.
                early_attempts: list[str] = []

                async def look_early():
                    with capture_answer_attempts(early_attempts):
                        return await think(question, pointing)

                early = Look(question, view, moment, tool_text, asyncio.create_task(look_early()), time.perf_counter(),
                             request_id=budget.request_id, attempt_ids=early_attempts,
                             log=getattr(self.agent, "log", None), admission=budget)
                current().track(early.task)  # the request's views stay held while it runs
                vision_task, vision_at = early.task, early.started
            try:
                quick = await self.quick(question, moment=moment) if deciding else None
            except BaseException:
                if early is not None:
                    early.drop()  # the request is gone (cancelled): its look goes with it
                raise
            if early is not None and quick is not None and (not quick.needs_look or quick.actions):
                # The notes answer it, or its steps change the board the look planned on (a look that knows them
                # replaces it, below): dropped and refunded before the steps or any query round are sent, so the
                # acquired-inputs path (ee959f12) keeps every call it had.
                early.drop_for_request()
                await asyncio.gather(early.task, return_exceptions=True)
                self._log_early_look(early, channel, quick, used=False)
                early, vision_task, vision_at = None, None, None
            if quick is not None and (not quick.needs_look or quick.actions):
                # What can be done from the notes is done now ("close the zoom"); a highlight of
                # something the look saw before the slide moved waits for this request's look,
                # without holding the rest back (live session 2026-10-04 15:27:50).
                now, later = self.split_for_look(quick.actions)
                pre_ctx = ToolContext(channel, user_turn.id, picture_id, question, delegation_id, [], when_asked=when_asked,
                                      seen_view=quick.view, asked=asked_in, **origin_context)
                pre_ctx.retain_goal(getattr(quick, "goal", None), quick.actions)
                numbers = None
                if pre_ctx.goal is not None or any(dependent(action) for action in quick.actions):
                    # split_for_look keeps order but may omit slots. Goal.action
                    # retains the full original reply's ordinals, not this subset's.
                    original = iter((action_number(action, i), action) for i, action in enumerate(quick.actions, 1))
                    numbers = [next(i for i, action in original if action is step) for step in now]
                # Fast effects otherwise wait through their inline deadline before
                # reaching the ordinary draft path. Offer the actual model plan
                # before that wait; a query plan still needs its actual results.
                offer_draft(quick.answer, quick.actions,
                    unavailable=observation_limit(quick.observation_data(), camera_pixels=False,
                                                  require_scope=request_snapshot is not None))
                pre_runs = await self.run_actions(now, pre_ctx, numbers)
        selected_camera = bool(pre_runs) and any(r.tool == 'inspect_camera' for r in pre_runs)
        acquired_plan = (vision_task is None and quick is not None and bool(pre_runs) and not later
                         and (selected_camera or (all(r.ok is not None for r in pre_runs)
                              and not needs_another_look(pre_runs)
                              and any(r.ok is True and self.registry.kind(r.tool) == "query" and r.images
                                      for r in pre_runs))))
        from .claims import camera_input_limit
        camera_needed = bool(acquired_plan and ((input_capture is None and quick.needs_look) or selected_camera))
        input_limit = camera_input_limit(camera_needed, ())
        # Keep the original decision/context/slots and actual receipts. This path's
        # selected pixels establish their own channels; an omitted camera part stays unmet.
        if quick is not None and pre_runs is not None and not acquired_plan and (later or quick.needs_look):
            # Done now what needed no look; the look does the rest (and is not told to do it again).
            carried = [r for r in pre_runs if r.ok is not False]
            superseded = [r for r in pre_runs if r.ok is False]
            carried_ctx = pre_ctx
            preceding = tuple(pre_runs)
            quick.needs_look, pre_runs, pre_ctx = True, None, None
            if vision_task is None:
                vision_task = asyncio.create_task(think(question, pointing, preceding=preceding))
                vision_at = time.perf_counter()
        if quick is not None and pre_runs is not None and not acquired_plan and needs_another_look(pre_runs):
            # A highlight missed (the slide moved since the look, or nothing there could be
            # outlined) or nothing worked: look now. What did work stays done and is not
            # repeated. The original failed attempts remain facts beside whatever
            # the later look does; a new outcome does not erase an earlier one.
            carried = [r for r in pre_runs if r.ok is not False]
            superseded = [r for r in pre_runs if r.ok is False]
            carried_ctx = pre_ctx
            preceding = tuple(pre_runs)
            quick.needs_look, pre_runs, pre_ctx = True, None, None
            if vision_task is None:
                vision_task = asyncio.create_task(think(question, pointing, preceding=preceding))
                vision_at = time.perf_counter()
        if quick is not None and (not quick.needs_look or acquired_plan):
            if vision_task is not None:
                vision_task.cancel()
                await asyncio.gather(vision_task, return_exceptions=True)
            if compare is not None:
                compare.cancel()
                await asyncio.gather(compare, return_exceptions=True)
                compare = None
            data = {"answer": quick.answer, "targets": [], "actions": [],
                    **quick.observation_data(),
                    "goal": getattr(quick, "goal", None)}
            # Fast already checked the raw slots; these steps ran before this reply.
            goal_actions = actions_wire(quick.actions)
            used = {"latency_ms": quick.ms, "model": quick.model, "fast": True}

            async def think(q: str, pointing_now: str, phase="query"):  # noqa: F811  (follow-ups stay on notes)
                again = await self.quick(q, followup=True, when_asked=when_asked,
                                         moment=self.moment(when_asked, request_snapshot=request_snapshot))
                return ({"answer": again.answer, "actions": actions_wire(again.actions),
                         **again.observation_data(),
                         "goal": getattr(again, "goal", None), "stated": getattr(again, "stated", None)} if again else {},
                        {"latency_ms": again.ms if again else 0.0, "model": quick.model, "fast": True})
        else:
            if vision_task is None:
                vision_task = asyncio.create_task(think(question, pointing))
                vision_at = time.perf_counter()
            if early is not None and vision_task is early.task:
                early.used = True  # the look this request needed, already running since the request was made
                self._log_early_look(early, channel, quick, used=True)
            try:
                data, used = await vision_task
            except AdmissionDenied:
                data = {"answer": ""}
                used = {"latency_ms": 0.0, "model": settings.vision}
            except BaseException:
                tools.resolve_first_guess(self.hub, guess, [])
                raise
            goal_actions = data.get("actions")
        think_ms = used["latency_ms"]
        calls = budget.used

        scope_limit = observation_limit(data, camera_pixels=input_capture is None and view is not None and not used.get("fast"),
                                        images=[(input_capture.analysis_bytes, scientific_ask.facts(input_capture))] if input_capture is not None and not used.get("fast") else (),
                                        require_scope=request_snapshot is not None)
        answer = "" if scope_limit else str(data.get("answer") or "").strip()
        answer_limits = [scope_limit] if scope_limit else []
        if view is None or input_capture is not None:
            data["targets"] = []  # notes cannot point at absent camera pixels
        # The steps that check a target: the look's, or the fast pass's when it acted (M2.1 re-check SF3), and
        # only for targets this answer has (the look's own number, or the fast pass's outlines).
        stepped = parse_actions(data.get("actions")) or (list(quick.actions) if used.get("fast") and quick is not None else [])
        n_targets = len([t for t in (data.get("targets") or []) if isinstance(t, dict)]) if not used.get("fast") else \
            len(getattr(pre_ctx, "objects", []) or [])
        guessed = [n for n in first_guess_targets(stepped) if n <= n_targets]
        if guessed:
            answer_limits.append(f"Targets {guessed} are first guesses by eye; their measurement checks have not completed at planning time.")
        # An eligible effect/target reply is a model plan, not completed-work
        # proof. Query results and failed prior steps need their answering round.
        draft_runs = [*(pre_runs or ()), *carried, *superseded]
        offer_draft(answer, stepped, draft_runs, unavailable=scope_limit or input_limit)
        subjects = self.services.subjects if self.services is not None else None
        if input_capture is None and data.get("specimen") and subjects is not None:
            # The subject is the confirmed one (two looks, or what the user named): an answer's
            # own guess fills it only when there is none yet (day/subject.py).
            subjects.guess(str(data["specimen"]))
        targets = [t for t in (data.get("targets") or []) if isinstance(t, dict)][:MAX_TARGETS]
        if used.get("fast"):
            targets = []  # the fast pass acts on what is already outlined or seen
        outline_errors: list[str] = []
        ctx = pre_ctx or ToolContext(channel, user_turn.id, picture_id, question, delegation_id, [], when_asked=when_asked,
                                     seen_view=seen_view, asked=asked_in, **origin_context)
        ctx.admission = budget
        for name, value in origin_context.items():
            if name == "voice_owner" or value is not None or getattr(ctx, name, None) is None:
                setattr(ctx, name, value)
        if carried_ctx is not None:
            ctx.carry_goal_from(carried_ctx)
            ctx.inspection_slots = carried_ctx.inspection_slots
            ctx.camera_selection = carried_ctx.camera_selection or ctx.camera_selection
            ctx.objects.extend(i for i in carried_ctx.objects if i not in ctx.objects)
            ctx.pointed = ctx.pointed or carried_ctx.pointed  # what the steps done at once pointed at
            # What hands' steps showed in the one magnifier is the request's too (G4 review, SHOULD-FIX 1).
            ctx.magnified[:0] = [i for i in carried_ctx.magnified if i not in ctx.magnified]
        ctx.retain_goal(data.get("goal"), stepped, raw_actions=goal_actions)
        objects = []
        if pre_runs is not None:
            runs = pre_runs
        else:
            # The look's steps that repeat one hands already did are not done again; only those (G4 review,
            # NOTE 5: hands zoomed one stoma, and the look's zoom on the other was dropped with it).
            done_steps = repeated_occurrences(parse_actions(data.get("actions")), carried,
                                        getattr(carried_ctx, "aliases", None) or {})
            # Each step keeps the number the model gave it ("@2" is the answer's second step, however
            # its steps are run).
            numbered = [(action_number(action, i), action)
                        for i, action in enumerate(parse_actions(data.get("actions")), start=1)
                        if action_number(action, i) not in done_steps]
            actions = [a for _, a in numbered]
            # The look's outlines come after the request's clearing steps and before the rest: a
            # "clear" in the same answer must not remove what the answer outlines (live session
            # 2026-10-04 14:06:52), and "zoom target 1" needs target 1.
            # Of the choices (`use`), only the first of each operation goes first: a later one is for the steps after it.
            choosing_first = {id(numbered[k][1]) for k in first_choices([a for _, a in numbered])}
            first_n = [(i, a) for i, a in numbered if (a[0] in CLEARING or id(a) in choosing_first)
                       and not dependent(a)] if targets else []
            rest_n = [(i, a) for i, a in numbered if (i, a) not in first_n]
            first, rest = [a for _, a in first_n], [a for _, a in rest_n]
            if first and actions[:len(first)] != first:
                log_override(self.agent.log, "clearing-before-outlines", question=question,
                             asked=[n for n, _ in actions], ran=[n for n, _ in first] + ["(outlines)"] + [n for n, _ in rest])
            ctx.steps = Steps.carrying(parse_actions(data.get("actions")), done_steps,
                                       getattr(carried_ctx, "steps", None) if carried else None)
            # The whole plan is known before its clearing steps run apart from the rest: what a later step names is kept.
            ctx.steps.planned.update((i, (a[0], a[1])) for i, a in numbered)
            runs = carried + await self.run_actions(first, ctx, [i for i, _ in first_n])
            before = {o.id: o for o in self.hub.scene.objects.values() if o.kind == "highlight" and o.trial is None}
            by_index: list = []
            import inspect

            extra = {"by_index": by_index} if "by_index" in inspect.signature(self.agent.point).parameters else {}
            if "own" in inspect.signature(self.agent.point).parameters:
                # What this request's own steps outlined (hands' finds, the steps before): its look never replaces them.
                extra["own"] = frozenset(ctx.objects)
            objects = await self.agent.point(targets, info, crop, used.get("marks"), used["model"],
                                             errors=outline_errors, **extra) if targets else []
            # These are actual Agent.point results, not unresolved ctx.objects
            # from a pending/failed tool. Freeze and file their producing facts
            # before later actions or model interpretation can change the board.
            pointed_outputs = [{**source, "turn_id": user_turn.id} for obj in objects
                               if (source := context._outline_source(self.hub, obj.id)) is not None]
            if (pointed_outputs and asked_in.routing_generation == self.conversation.store.generation
                    and asked_in.thread in self.conversation.store.threads):
                self.conversation.add("note", channel, "Look targets outlined.", asked=asked_in,
                                      objectIds=[row["id"] for row in pointed_outputs], outputRefs=pointed_outputs)
            ctx.objects[:0] = [o.id for o in objects if o.id not in ctx.objects]
            ctx.targets = by_index
            ctx.aliases.update(replaced_by(before, objects, self.hub.scene.objects))
            await mark_first_guesses(self.hub, guessed, ctx)
            if objects and self.services is not None:
                # The look's own outlines while the outlines are hidden: shown again, and said (re-check N7).
                from .toolbox.present import reshow_outlines

                shown_again = await reshow_outlines(self.services, ctx)
                if shown_again:
                    answer_limits.append(shown_again)
            runs += await self.run_actions(rest, ctx, [i for i, _ in rest_n])
            if superseded:
                # Keep actual original failures and their occurrence/source facts.
                # Later pointing is useful work, not a replacement for those attempts.
                runs = superseded + runs
        tools.resolve_first_guess(self.hub, guess, objects)

        # Carried Hands work may have settled before its user turn was bound.
        # File its original outputs now, before any outcome-model await. Already
        # filed occurrences remain the same journal receipt, not a second reply.
        for run in runs:
            self._remember_run_outputs(run, carried_ctx if carried_ctx is not None
                                       and any(run is earlier for earlier in carried) else ctx)

        # The request's steps have run (or report late on their own rows): what is left is writing its answer. Said, so
        # the chat's working line follows the request's state, not one switch for the whole turn (live 2026-10-11: the
        # photo was on screen 6.9 s in, and "Working..." stayed 18.7 s more while the answer was written).
        if channel == "chat":
            with contextlib.suppress(Exception):
                self.hub.broadcast({"type": "thinking", "on": True, "phase": "answering"})
        # A successful effect is not necessarily the whole request. The live
        # companion's existing outcome round keeps its tools, so it can finish
        # omitted work rather than only rewrite prose about a partial plan.
        asked = [r for r in runs if self.registry.kind(r.tool) == "query"]
        deferred_queries = False
        completed_plan = list(runs)
        switched = any(r.tool in MOVES and r.ok for r in [*runs, *carried])
        needs_execution = (quick is not None and quick.intent == "request" and not quick.actions
                           and not runs and not ctx.objects and not targets and not superseded)
        needs_recovery = any(r.ok is False for r in runs)
        # COMPLETION "unfinished": a plan that worked is the answer (a failure or a query still goes back).
        needs_completion = bool(runs) and bool(used.get("fast")) and settings.completion == "every-plan"
        guarded_continuation = needs_execution or needs_recovery or needs_completion
        retired_continuation = False
        def continuation_current():
            nonlocal retired_continuation
            from .toolbox.resources import require_current
            try:
                require_current(ctx, self.day)
                return True
            except ToolFailed as exc:
                # A read-only continuation must propagate retirement to its caller.
                # Completed effects already filed to the original journal remain
                # retained without publishing a new answer in the replacement day.
                if not any(r.ok and self.registry.kind(r.tool) == 'effect' for r in completed_plan):
                    raise
                retired_continuation = True
                self._interpretation_failed(ctx, "query", "request-retired", error=exc)
                return False
        answered_completed = False
        completed_answer_facts = None
        completed_stated = None  # (the ids its completion round was given, the ids its answer said it stated)
        outcome_round = False  # the last call is the outcome round over the receipts (below)
        if (asked or needs_recovery or needs_completion) and budget.used < MAX_CALLS:
            final_hold = budget.reserve_final()
            # Only an effect of the latest plan that failed: an earlier plan's failure that a look already re-planned
            # (`superseded`) has had its recovery, and query results keep the reserved call to be interpreted.
            failed_now = any(r.ok is False and self.registry.kind(r.tool) != "query"
                             and not any(r is s for s in superseded) for r in runs)
            if failed_now and budget.remaining == 0:
                # A step failed and only the reserved outcome call is left (live 2026-10-10 20:33: the zoom failed and
                # its recovery never got a call). That call becomes the outcome round over the receipts, on the notes:
                # the model either says what happened (as the final answer would) or adds steps for what is
                # unfinished ("find the eye first").
                budget.release_final(final_hold)
                final_hold = None
                outcome_round = True
        while budget.remaining > 0 and (asked or needs_execution or needs_recovery or needs_completion):
            needs_execution = False
            needs_recovery = False
            needs_completion = False
            if guarded_continuation:
                if not continuation_current():
                    break
            if any(r.ok is None for r in runs):
                # These are not query results yet. A new model round would replace the step
                # references that this plan still needs; finish its answer when the plan lands.
                deferred_queries = True
                break
            from .claims import answer_facts
            inspected = [image for run in completed_plan if run.ok for image in run.images][:2]
            camera_needed = camera_needed or any(r.tool == 'inspect_camera' for r in completed_plan)
            input_limit = camera_input_limit(camera_needed, inspected)
            # The camera crop goes only to a look of this round: the outcome round answers on the notes, with no picture
            # (live 2026-10-11 16:36:22: it was told the crop was supplied, and its answer then never compared equal to
            # the same settled receipts, so it stood unchecked).
            camera_pixels = (input_capture is None and view is not None and not used.get("fast") and not inspected
                             and not (outcome_round and settings.fast != "off"))
            supplied = inspected or ([(input_capture.analysis_bytes, scientific_ask.facts(input_capture))]
                                     if input_capture is not None and not (outcome_round and settings.fast != 'off') else [])
            results = answer_facts(completed_plan, images=supplied, camera_pixels=camera_pixels,
                                   limitations=[*answer_limits, input_limit], targets=len(targets), outlined=len(objects))
            # A fact about the loop, said to the model (facts, not verdicts): nothing runs after this answer, so
            # an answer that only promises ("let me find them") leaves the user with nothing done (live, flow 5).
            # Measured (bench/brain_prompt.py --followup, Gemini 3.5 Flash, 6 each): "pull up the cells from
            # earlier" shown with present 6/6, against 1/6 as it was and 0/6 with a longer instruction.
            follow = results_followup(question, results)
            # The board as the steps so far left it (what they outlined, opened or closed).
            tool_text = (self.registry.prompt_section(), *context.scene_table(self.moment(ctx.when_asked, pushes=True, request_snapshot=ctx.request_snapshot)))
            if input_limit:
                follow += '\n' + input_limit
            # The ids its answer states, as the answer written from the receipts would be given them (stated_block):
            # an answer that states them all stands, with no call after it (journeys round 2, the lead's (a)).
            completion_ids = stated_ids(completed_plan, len(targets), len([o for o in objects if getattr(o, "shown", True)]),
                                        outline_errors, bool(used.get("fast")),
                                        [f"{o.label or 'an outline'} not shown yet: {o.not_shown_why()}"
                                         for o in objects if not getattr(o, "shown", True)])
            if completion_ids:
                follow += "\n" + stated_block(completion_ids)
            try:
                if inspected:
                    from .toolbox.resources import require_current
                    require_current(ctx, self.day)
                    from ..inspection import image_context
                    from ..prompts import RESOURCE_BASIS
                    prompt = ("Answer from these supplied pictures and app receipts. Each manifest identifies retained-source, specimen-camera or verified workspace pixels. Use only the supplied channel/source.\n"
                              + image_context(inspected) + "\n" + RESOURCE_BASIS + "\n" + "\n".join(tool_text) + "\n" + follow
                              + '\nReply with JSON: {"answer":"...", "actions":[{"tool":"...", "args":{}}]}. Do not point in the live view.')
                    if input_limit:
                        prompt += ("\n" + input_limit + " The feed's availability is distinct from these supplied inputs. "
                                   "Retain this unmet part in the answer; do not claim the whole comparison was fulfilled.")
                    reply = await self._answer_model(settings.vision, [image for image, _ in inspected], prompt, phase="query",
                                                     max_output_tokens=PLANNER_OUTPUT_TOKENS)
                    require_current(ctx, self.day)
                    data, used = reply.data or {}, {"latency_ms": reply.latency_ms, "model": settings.vision, "savedImages": True}
                elif outcome_round and settings.fast != "off":
                    outcome_round = False  # the last call: the receipts and the notes, no new picture
                    again = await self.quick(follow, followup=True, when_asked=ctx.when_asked,
                                             moment=self.moment(ctx.when_asked, request_snapshot=request_snapshot))
                    if again is None:
                        raise tools.ToolError("the outcome round did not answer", next="ask again")
                    data = {"answer": again.answer, "actions": actions_wire(again.actions), **again.observation_data(),
                            "goal": getattr(again, "goal", None), "stated": getattr(again, "stated", None)}
                    used = {"latency_ms": again.ms, "model": again.model, "fast": True}
                else:
                    data, used = await think(follow, "box", "query")
            except Exception as exc:
                from .toolbox.resources import require_current
                if (guarded_continuation or inspected) and not continuation_current():
                    break
                self.agent.log({"kind": "answer-continuation-failed", "question": question,
                                "error": f"{type(exc).__name__}: {exc}", "calls": budget.used})
                break  # the final answering opportunity uses the same spent allowance
            if guarded_continuation:
                if not continuation_current():
                    break
            think_ms += used["latency_ms"]
            calls = budget.used
            scope_limit = observation_limit(data, camera_pixels=input_capture is None and view is not None and not used.get("fast") and not inspected,
                                            images=inspected or ([(input_capture.analysis_bytes, scientific_ask.facts(input_capture))] if input_capture is not None and not used.get("fast") else ()), require_scope=request_snapshot is not None)
            answer = "" if scope_limit else str(data.get("answer") or "").strip()
            if scope_limit:
                answer_limits.append(scope_limit)
            next_actions = parse_actions(data.get("actions"))
            ctx.retain_goal(data.get("goal"), next_actions, raw_actions=data.get("actions"))
            answered_completed = bool(answer) and bool(completed_plan) and not next_actions and all(r.ok is not None for r in completed_plan)
            completed_answer_facts = results if answered_completed else None
            completed_stated = (completion_ids, data.get("stated")) if answered_completed else None
            more = await self.run_actions(next_actions, ctx)
            completed_plan.extend(more)
            runs = [r for r in runs if self.registry.kind(r.tool) != "query"] + more
            asked = [r for r in more if self.registry.kind(r.tool) == "query"]
            needs_recovery = any(r.ok is False for r in more)
            if budget.used < MAX_CALLS and (asked or any(r.ok is False for r in more)):
                # A continuation tool may have created a new hold after no-action
                # planning released the old one. Retrieve that same opaque token.
                final_hold = budget.reserve_final()
            switched = switched or any(r.tool in MOVES and r.ok for r in more)
        calls = budget.used
        deferred_queries = deferred_queries or any(r.ok is None for r in runs)
        runs = completed_plan  # keep successful and failed receipts for reconciliation, UI and the log
        camera_needed = camera_needed or any(r.tool == 'inspect_camera' for r in runs)
        final_images = [picture for run in runs if run.ok for picture in run.images][:2]
        if (not camera_needed and not answered_completed and not final_images and isinstance(info, Capture)
                and getattr(info, 'boot', None) is not None and getattr(info, 'seq', None) is not None
                and not used.get('fast') and (runs or objects)):
            # Keep the camera evidence used to perform the request through its
            # outcome response. This is the original frame, never a claim of a
            # new look or workspace screenshot.
            from ..inspection import inspect_camera
            try:
                final_images = [await inspect_camera(self.hub, info, timing='original request camera frame')]
            except (ToolFailed, Refused):
                pass  # concrete receipts still describe what succeeded
        input_limit = camera_input_limit(camera_needed, final_images)
        guarded_outcome = guarded_continuation or any(r.resource for r in runs)

        if switched and self.conversation.here() != asked_in:
            # The user went back to another conversation in this request ("back to the blood
            # smear: what did we conclude?"), or to another session or a new one ("back to the leaf session: what did
            # we conclude?"): the request and its answer belong where the user is now.
            asked_in = self.conversation.here()
            self.conversation.add("user", "voice" if channel == "voice" else channel, said, asked=asked_in)
        # The answer was written before the tools ran: make it say what actually happened.
        shown = [o for o in objects if getattr(o, "shown", True)]
        unshown = [f"{o.label or 'an outline'} not shown yet: {o.not_shown_why()}" for o in objects if not getattr(o, "shown", True)]
        # Reuse an eligible model answer that already saw these exact completed
        # outcomes. Existing reconciliation still checks failures, unshown
        # targets, pending work, limitations and changed researcher facts.
        from .claims import answer_facts

        reuse_completed = answered_completed and completed_answer_facts == answer_facts(
            runs, images=final_images,
            limitations=[*answer_limits, input_limit], targets=len(targets), outlined=len(shown))
        same_answer = reuse_completed and moment.researchers == self.moment(ctx.when_asked).researchers
        # The ids an answer states (stated_ids): the completion round's answer stands only when it was given these same
        # ids and its `stated` names every one; otherwise it is written again from the receipts (the lead's (a)).
        # What the answer was given besides the receipts, where a model it names may come from (stated_ids): its notes
        # and the models of this request's own looks and drawings. Never the user's words (T4: "use the dino model").
        from ..names import said_impl

        also = [table, *(VISION[m].label for m in dict.fromkeys((used.get("model"), getattr(quick, "model", None)))
                         if m in VISION),
                *(said_impl(o.made_by.get("implementation")) for o in objects
                  if isinstance(getattr(o, "made_by", None), dict) and o.made_by.get("implementation")),
                *self._defaults_of_choices(runs)]
        ids_now = stated_ids(runs, len(targets), len(shown), outline_errors, bool(used.get("fast")), unshown,
                             answer=answer, also=also)
        stated_all = completed_stated is not None and completed_stated[0] == ids_now and states_all(ids_now, completed_stated[1])
        # A completion reply without the list confirms nothing either way: it stands, logged as unchecked, as the
        # answer rewrite's own does (ADR 0006's amendment; _said_everything). Only a list that leaves an id out is a
        # confirmed omission, written again (the 2026-10-11 gate: a stand-in reply with no list cost every failed
        # step after a completion round one more call).
        if (same_answer and completed_stated is not None and ids_now and completed_stated[0] == ids_now
                and not isinstance(completed_stated[1], list)):
            stated_all = True
            log_override(self.agent.log, "answer-recheck", question=question, answer=answer, unchecked=list(ids_now),
                         kept=True, where="completion")
        # Written again after a completion round that answered these same settled receipts: if that call fails, the
        # completion round's answer (the model's grounded words) is kept rather than no prose (the lead's (b)).
        kept_if_unavailable = answer if same_answer and ids_now else ""
        if (not kept_if_unavailable and answer and (runs or objects) and not ids_now and not answer_limits
                and not input_limit and not any(r.ok is None for r in runs)
                and not any(self.registry.kind(r.tool) == "query" or r.resource for r in runs)
                and moment.researchers == self.moment(ctx.when_asked, request_snapshot=ctx.request_snapshot).researchers):
            # A request whose steps ran never ends with no words (T5, live 2026-10-11 16:49:38: the eye outlined and
            # shown, the answer written again from the receipts dropped as unsupported-input, and nothing said). With
            # nothing to tell beyond what it said, as ANSWER_CHECK "contradicted" judges it (no problem or choice id,
            # nothing still running, no limitation, no query result to interpret, no saved resource whose identity
            # the rewrite checks, no researcher changed), the request's own model answer is kept if that call can't be
            # had (answer-recheck, unchecked, with the reason). Otherwise no prose stays the rule (ADR 0006's amendment).
            kept_if_unavailable = answer
        if retired_continuation:
            answer = ""  # keep original receipts; no new actions/prose in a replacement day
        elif any(r.ok is None for r in runs):
            # Keep the reserved outcome round for the original work's settled
            # facts. A pending effect can finish during a model await; spending
            # the last slot here would leave its progress answer terminal.
            if any(r.ok is None and self.registry.kind(r.tool) != "query" for r in runs):
                if budget.remaining > 0 and not any(
                        r.ok is None and self.registry.kind(r.tool) == "query" for r in runs
                ):
                    # A spare, unreserved round can preserve useful progress and
                    # correct changed status now without spending the final outcome.
                    answer = await self._reconcile(question, answer, runs, len(targets), len(shown),
                        outline_errors, bool(used.get("fast")), unshown, before=moment,
                        may_ask=True, reservation=None, when_asked=ctx.when_asked,
                        images=final_images, ctx=ctx, guard_ownership=guarded_outcome,
                        input_limit=input_limit, limitations=answer_limits, also=also, progress=True)
                    if not any(r.ok is None for r in runs):
                        # Every step it was told was in progress finished while it was written: "still in progress"
                        # is never said of a finished step (T4, live 2026-10-11 16:28:55, 16:42:22, 16:42:57: dissect
                        # finished ~1.5 s into the call, and "it is still in progress ... will finish shortly" went out
                        # with the result a second later). Written again from the settled receipts on the reserved
                        # call the late answer would have used; no late answer follows.
                        log_override(self.agent.log, "progress-settled", question=question, answer=answer)
                        answer = await self._reconcile(question, answer, runs, len(targets), len(shown), outline_errors,
                                                       bool(used.get("fast")), unshown, before=moment, force=True,
                                                       may_ask=budget.used < MAX_CALLS, reservation=final_hold,
                                                       when_asked=ctx.when_asked, images=final_images, ctx=ctx,
                                                       guard_ownership=guarded_outcome, input_limit=input_limit,
                                                       limitations=answer_limits, also=also)
                        deferred_queries = False
                else:
                    answer = ""  # actual Activity receipts carry progress; no invented outcome prose
        elif same_answer and (not ids_now or stated_all):
            # This model answer already interpreted these exact settled outcomes and stated every problem and model
            # choice by id. One that left a choice out is written again (live 2026-10-11, journey 17 typed 0 of 3: the
            # completion round's "Redrew stoma 1 using SAM 2.1 tiny" stood, never saying it was for this request only).
            pass
        elif answered_completed and budget.used >= MAX_CALLS and (not ids_now or stated_all):
            pass  # The outcome round already answered these settled receipts, stating every id; no call remains.
        elif answered_completed and budget.used >= MAX_CALLS:
            # The outcome round's answer left a problem or choice out (its `stated`), or was given other ids: with the
            # three calls spent it is written again from the receipts on the answer's own last call
            # (reserve_last_word), never taken as it was (live 2026-10-11 16:36:22: "re-highlighted the vascular
            # bundle" stood with its highlight failed, and no record said so).
            answer = await self._reconcile(question, answer, runs, len(targets), len(shown), outline_errors,
                                           bool(used.get("fast")), unshown, before=moment, force=True, may_ask=False,
                                           when_asked=ctx.when_asked, images=final_images, ctx=ctx,
                                           guard_ownership=guarded_outcome, input_limit=input_limit,
                                           limitations=answer_limits, kept=kept_if_unavailable, also=also)
        elif asked and calls >= MAX_CALLS and not any(r.ok is None for r in runs):
            # The final round's query ran after its draft. No synthesis budget remains, so that draft
            # cannot describe the unseen result (e.g. saying c9 when source selection actually chose c8).
            # Keep actual receipts, including earlier failures and failed outlines, without another model call.
            log_override(self.agent.log, "query-budget-exhausted", question=question, answer=answer,
                         actions=[r.wire() for r in runs])
            # The answer's last word (one call beyond the three, once): a model reads these results and says them.
            answer = await self._reconcile(question, answer, runs, len(targets), len(shown), outline_errors,
                                           bool(used.get("fast")), unshown, before=moment, force=True, may_ask=False,
                                           when_asked=ctx.when_asked, images=final_images, ctx=ctx,
                                           guard_ownership=guarded_outcome, input_limit=input_limit,
                                           limitations=answer_limits, also=also)
        else:
            # A successful operation may still select the wrong resource. Query answers and a model-classified
            # request with no executed steps need outcome grounding too, within this turn's existing call budget.
            grounded = guarded_continuation or bool(runs) or bool(objects) or bool(answer_limits) or (
                quick is not None and quick.intent == "request" and not runs)
            if settings.answer_check == "contradicted":
                # Only when what happened contradicts the answer (ANSWER_CHECK). Failed, unfinished or unshown steps
                # and a changed researcher are `_reconcile`'s own problems either way; here: a query whose result no
                # answer saw, a step that opened or made a saved resource (a success does not say it was the one
                # asked for: its identity is checked against the receipt, as before 2026-10-09), and a request that
                # did nothing. A completion round's answer saw its receipts.
                grounded = bool(answer_limits) or (quick is not None and quick.intent == "request" and not runs) or (
                    not answered_completed and any(self.registry.kind(r.tool) == "query" or r.resource for r in runs))
            if guarded_outcome:
                from .toolbox.resources import require_current
                require_current(ctx, self.day)
            answer = await self._reconcile(question, answer, runs, len(targets), len(shown), outline_errors, bool(used.get("fast")),
                                           unshown, before=moment, force=grounded and not deferred_queries and not reuse_completed,
                                           may_ask=budget.used < MAX_CALLS, reservation=final_hold, when_asked=ctx.when_asked,
                                           images=final_images, ctx=ctx,
                                           guard_ownership=guarded_outcome, input_limit=input_limit,
                                           limitations=answer_limits, kept=kept_if_unavailable, also=also)

        calls = budget.used
        answer = without_ids(answer, ids_now)
        if self.hub.settings.level != "technical":  # at the technical level, ids are the user's to read
            answer = without_app_ids(answer, self.is_app_id)
        if not deferred_queries and final_hold is not None:
            budget.release_final(final_hold)
        if not retired_continuation and (guarded_outcome or any(run.images for run in runs)):
            from .toolbox.resources import require_current
            require_current(ctx, self.day)
        if channel == "voice" and answer:  # context only, no claim a retry/look occurred
            self.conversation.add("note", "voice", f"Model answer for the original request: {answer}", objectIds=ctx.objects,
                                  resourceIds=[r.resource["id"] for r in runs if r.resource], asked=asked_in,
                                  outputRefs=[])
        elif answer:
            self.conversation.add("assistant", channel, answer, objectIds=ctx.objects,
                                  resourceIds=[r.resource["id"] for r in runs if r.resource], asked=asked_in,
                                  outputRefs=[])
            await self.note_to_voice(f"Chat log, nothing to say about it: the user typed a request ({question}) and the "
                                     f"app already answered in writing ({answer}).")

        result = {
            "answer": answer,
            "answerStatus": "model" if answer else "not-published",
            "turnId": user_turn.id,
            "asked": asked_in,  # not for the wire: the server files the shown answer with it
            "objectIds": ctx.objects,
            "targets": len(targets),  # the things its look asked to outline: an outline was asked for when any
            # Retain each actual step, including failed attempts before useful
            # later work; a later outline does not replace the earlier receipt.
            "actions": [r.wire() for r in runs],
            "specimen": self.hub.scene.context.get("specimen"),
            "model": (f"{VISION[used['model']].label}, from the workspace notes" if view is None else
                      f"{VISION[used['model']].label}, from what it had seen" if used.get("fast") else VISION[settings.vision].label),
            "fast": bool(used.get("fast")),
            "thinkMs": round(think_ms),
            "totalMs": round((time.perf_counter() - started) * 1000),
            "calls": calls,
            "options": settings.to_wire(),
        }
        # Activity: the request's steps, the answering model and the look's own outlines (what found and drew them).
        activity.request_finished(self.hub, result, look=objects, look_model=used.get("model") if objects else None)
        made = [self.hub.scene.objects[i] for i in ctx.objects if i in self.hub.scene.objects]
        self.agent.log({"kind": "turn", "channel": channel, "question": question, **settings.to_wire(),
                        # The settings above are the defaults; what this request chose and what really ran (2026-10-11).
                        "used": self._used(runs, made),
                        "thinkMs": result["thinkMs"], "totalMs": result["totalMs"], "calls": calls, "answer": answer,
                        # Every outline the request made, its look's and its steps' (the look's alone: lookOutlined).
                        "targets": len(targets), "outlined": len(ctx.objects), "lookOutlined": len(objects),
                        "labels": [o.label for o in made],
                        "outlineErrors": outline_errors, "retried": [r.wire(full=True) for r in superseded],
                        "actions": [r.wire(full=True) for r in runs], "fastUsed": result["fast"],
                        "delegationId": ctx.delegation_id,
                        "handsUsed": handled is not None, "lookReused": look is not None,
                        # ms from the turn starting to the vision call starting (None: no look)
                        "visionStartMs": round((vision_at - started) * 1000) if vision_at is not None else None,
                        "frameId": picture_id, "epoch": info.epoch if info is not None else None, "objectIds": list(ctx.objects),
                        "confidences": [o.confidence for o in made],
                        "image": await asyncio.to_thread(self.agent.keep_view, "turn", image, crop, info.id)
                        if view is not None and hasattr(self.agent, "keep_view") else None})  # the log keeps every step
        for hook in self.on_answer:
            try:
                hook(result)
            except Exception:
                pass
        if compare is not None:
            result["compare"] = compare
        if deferred_queries:
            task = asyncio.create_task(self._answer_queries_later(ctx, tuple(completed_plan), moment.learner,
                                                                 final_hold, input_limit=input_limit, camera_needed=camera_needed,
                                                                 outlined_count=len(shown), unshown=tuple(unshown), target_count=len(targets),
                                                                 outline_errors=tuple(outline_errors)))
            if current() is not None:
                current().track(task)
            self._query_answers = {t for t in getattr(self, "_query_answers", set()) if not t.done()} | {task}
        return result

    async def _answer_queries_later(self, ctx: ToolContext, runs: tuple[ToolRun, ...], learner: str, reservation=None,
                                   *, input_limit="", camera_needed=False, outlined_count=0, unshown=(), target_count=0,
                                   outline_errors=()) -> None:
        """Join original results, then offer only the original remaining planning allowance.

        A reserved final round never authors effects. An available query round
        may choose new actions from completed facts, just as the inline path does.
        Successful original work is not replayed; no new camera look is implicit.
        """
        from ..prompts import QUERY_ANSWER
        from ..ticket import current
        from .claims import answer_facts, camera_input_limit, observation_limit
        from .toolbox.resources import require_current

        request = current()
        sequences = {r.sequence for r in runs if r.sequence is not None}
        await asyncio.gather(*(asyncio.shield(t) for t in sequences), return_exceptions=True)
        if request is not None and request.superseded("session"):
            return
        queries = [r for r in runs if self.registry.kind(r.tool) == "query"]
        if not runs or any(r.ok is None for r in runs):
            return  # an interrupted plan has no completed query to answer from
        completed = list(runs)
        images = [image for run in runs if run.ok for image in run.images][:2]
        input_limit = camera_input_limit(camera_needed or bool(input_limit), images)
        # Direct camera pointing and a tool may finish independently. Preserve
        # both original outcomes: a successful find does not erase an earlier
        # placement refusal, and that refusal is not the find's outcome.
        # These values were frozen before handing off: a later rename, carry or
        # re-anchor must not rewrite this request's original pointing outcome.
        pointing_limits = [f"Earlier direct camera outline: {why}" for why in unshown]
        if target_count and not outlined_count and not unshown:
            pointing_limits.append("Earlier direct camera outline: " +
                                   (outline_errors[0] if outline_errors else "nothing there could be outlined"))
        facts = answer_facts(runs, images=images, limitations=[input_limit, *pointing_limits],
                             targets=target_count, outlined=outlined_count)
        prompt = QUERY_ANSWER.format(question=ctx.question, learner=learner,
                                     results=facts)
        if ctx.request_snapshot is not None:
            prompt += "\n" + context._screen(self, for_voice=False, request_snapshot=ctx.request_snapshot)
        settings = self.hub.settings
        model = settings.fast if settings.fast != "off" else settings.vision
        if images:
            from ..inspection import image_context
            prompt += "\n" + image_context(images)
            model = settings.vision
        if input_limit:
            prompt += ("\n" + input_limit + " The feed's availability is distinct from these supplied inputs. "
                       "Retain this unmet part in the answer; do not claim the whole comparison was fulfilled.")
        answer = ""
        budget = ctx.admission
        try:
            if budget is not None and budget.used < MAX_CALLS:
                reservation = budget.reserve_final()  # acquisition may have completed after the turn returned
                require_current(ctx, self.day)
                if budget.remaining > 0:
                    before = self.moment(ctx.when_asked, request_snapshot=ctx.request_snapshot)
                    planning = (self.registry.prompt_section() + "\n"
                                + "\n".join(context.scene_table(self.moment(ctx.when_asked, pushes=True,
                                                                         request_snapshot=ctx.request_snapshot)))
                                + "\n" + results_followup(ctx.question, facts)
                                + '\nReply with JSON including answer, actions, observation_scope and input_refs. '
                                  'Use exact original output identities from these receipts; @step references in new actions '
                                  'refer only to this new action list. Do not point in the live view.')
                    if ctx.request_snapshot is not None:
                        from ..prompts import OBSERVATION_BASIS
                        planning += "\n" + context._screen(self, for_voice=False, request_snapshot=ctx.request_snapshot)
                        planning += "\n" + OBSERVATION_BASIS
                    if images:
                        from ..inspection import image_context
                        from ..prompts import RESOURCE_BASIS
                        planning += "\n" + image_context(images) + "\n" + RESOURCE_BASIS
                    if input_limit:
                        planning += "\n" + input_limit
                    data, limits, more = {}, [], []
                    try:
                        result = await asyncio.wait_for(self._answer_model(model, [image for image, _ in images], planning,
                            phase="query", max_output_tokens=PLANNER_OUTPUT_TOKENS), timeout=PLANNER_TIMEOUT_S)
                        require_current(ctx, self.day)
                        data = result.data or {}
                        limit = observation_limit(data, images=images, require_scope=ctx.request_snapshot is not None)
                        answer = "" if limit else str(data.get("answer") or "").strip()
                        if limit:
                            limits.append(limit)
                        actions = parse_actions(data.get("actions"))
                        ctx.retain_goal(data.get("goal"), actions, raw_actions=data.get("actions"))
                        more = await self.run_actions(actions, ctx)
                        completed.extend(more)
                        # Inline runs have no Runner late callback, but still
                        # need original activity when this whole turn is late.
                        for run in more:
                            if run.ok is not None:
                                await self._late(run, ctx)
                        sequences = {run.sequence for run in more if run.sequence is not None}
                        await asyncio.gather(*(asyncio.shield(task) for task in sequences), return_exceptions=True)
                        require_current(ctx, self.day)
                    except Exception as exc:
                        require_current(ctx, self.day)
                        self.agent.log({"kind": "answer-continuation-failed", "question": ctx.question,
                                        "error": f"{type(exc).__name__}: {exc}", "calls": budget.used})
                        answer = ""  # remaining final sees real outcomes, never a fabricated explanation
                    images = [image for run in completed if run.ok for image in run.images][:2]
                    camera_needed = camera_needed or any(run.tool == 'inspect_camera' for run in completed)
                    input_limit = camera_input_limit(camera_needed or bool(input_limit), images)
                    unchanged = facts == answer_facts(completed, images=images,
                        limitations=[input_limit, *pointing_limits], targets=target_count, outlined=outlined_count)
                    answer = await self._reconcile(ctx.question, answer, completed, target_count, outlined_count,
                        list(outline_errors), True, unshown,
                        before=before, force=not (answer and not more and unchanged and all(run.ok is True for run in completed)),
                        may_ask=budget.used < MAX_CALLS, reservation=reservation, when_asked=ctx.when_asked,
                        images=images, ctx=ctx, guard_ownership=True, input_limit=input_limit,
                        limitations=[*limits, *pointing_limits], include_tools=False)
                else:
                    # Only the protected interpretation remains. Its returned
                    # actions cannot run without an outcome-answer allowance.
                    attempts = []
                    try:
                        with capture_answer_attempts(attempts):
                            result = await asyncio.wait_for(self._answer_model(model, [image for image, _ in images], prompt,
                                phase="late", reservation=reservation), timeout=PLANNER_TIMEOUT_S)
                        require_current(ctx, self.day)
                        limit = observation_limit(result.data or {}, images=images,
                                                  require_scope=ctx.request_snapshot is not None)
                        answer = "" if limit else str((result.data or {}).get("answer") or "").strip()
                        if limit or not answer:
                            self._interpretation_failed(ctx, "late", "unsupported-input" if limit else "empty-answer", attempts)
                    except asyncio.CancelledError as exc:
                        self._interpretation_failed(ctx, "late", "canceled", attempts, exc)
                        raise
                    except Exception as exc:
                        reason = ("timeout" if isinstance(exc, asyncio.TimeoutError) else
                                  "request-retired" if isinstance(exc, ToolFailed) else
                                  "admission-denied" if isinstance(exc, AdmissionDenied) else "provider-error")
                        self._interpretation_failed(ctx, "late", reason, attempts, exc)
                        require_current(ctx, self.day)
        except ToolFailed:
            return  # original request retired; no newer context or voice owns its answer
        finally:
            if budget is not None and reservation is not None:
                budget.release_final(reservation)
        # Actual receipts publish independently. Only an eligible model response
        # fills the answer field; a timeout is not an assistant explanation.
        try:
            require_current(ctx, self.day)
        except ToolFailed:
            return
        self.agent.log({"kind": "query-answer", "turnId": ctx.turn_id, "question": ctx.question,
                        "answer": answer, "model": model, "calls": budget.used if budget is not None else 0, "actions": [r.wire(full=True) for r in completed]})
        if answer:
            self.conversation.add("note", ctx.channel, f"Model answer from the completed query: {answer}",
                                  resourceIds=[r.resource["id"] for r in completed if r.resource], asked=ctx.asked)
        if (answer or queries) and self.report_late is not None:
            await self.report_late(ctx, replace(completed[-1] if completed else queries[-1], answer=answer))

    async def _reconcile(self, question: str, answer: str, runs: list[ToolRun], n_targets: int, n_outlined: int,
                         outline_errors: list[str], fast: bool, unshown: list[str] = (), before: Moment | None = None,
                         force: bool = False, may_ask: bool = True, on_model_call=None, when_asked=None, images=(), ctx=None,
                         guard_ownership=False, reservation=None, input_limit="", limitations=(), include_tools=False,
                         kept: str = "", also=(), progress: bool = False) -> str:
        """Use the existing answer round to interpret real outcomes and inputs.

        No sentence classifier or receipt-to-prose fallback. An unavailable or
        incompatible interpretation leaves only the actual operation activity, except `kept`: the completion round's
        answer to these same settled receipts, which stands when this call fails (the lead's (b), 2026-10-11).
        `also`: what else the answer was given besides the receipts (stated_ids: the models it may name).
        `progress`: written while steps run; when they have all finished by the time it is written, its words are
        returned unchecked, for the caller to write again from the settled receipts (T4).
        """
        from ..prompts import RECONCILE

        from .claims import answer_facts, model_problems

        input_limits = [*limitations, *([input_limit] if input_limit else [])]
        ids = stated_ids(runs, n_targets, n_outlined, outline_errors, fast, unshown, answer=answer, also=also)
        problems = [p for i, p in ids.items() if i.startswith("p")]
        choices = [c for i, c in ids.items() if i.startswith("c")]
        from .claims import running

        pending = [running(r) for r in runs if r.ok is None]  # with what it is doing ("loading micro-sam ...")
        current = self.moment(when_asked, request_snapshot=getattr(ctx, "request_snapshot", None))
        research_changed = before is not None and before.researchers != current.researchers
        pending_effects = any(r.ok is None and self.registry.kind(r.tool) != "query" for r in runs)
        if not problems and not choices and not research_changed and not force and not pending_effects and not limitations:
            # Query-only progress is a model plan under the existing planning
            # contract. We do not infer completion semantics from its wording.
            return answer
        done = [f"{r.tool} {r.args}: {r.summary}" for r in runs if r.ok and r.summary]
        done += [f"{tool}: still in progress, not done yet" for tool in pending]
        log_override(self.agent.log, "answer-rewritten", question=question, answer=answer, problems=problems,
                     research_changed=research_changed, outcome_grounding=force)
        if n_outlined:
            done.append(f"outlined {n_outlined} thing{'s' if n_outlined != 1 else ''}")
        settings = self.hub.settings
        model = settings.fast if settings.fast != "off" else settings.vision
        # Each problem and choice has an id the reply names when it tells the user about it ("stated"; stated_ids, the
        # same ids the completion round is given): an omission is known from the reply itself, never judged by another call.
        prompt = RECONCILE.format(question=question, answer=answer or "(none)", problems=id_lines(ids, "p") or "- none",
                                  done="\n".join(f"- {d}" for d in done) or "- no new action or outline outcome was recorded in this response",
                                  state=context.reconciliation(current),
                                  image_basis=("The attached pixels and their channel/source manifests are the only images supplied. " if images else
                                               "You receive no image here; do not add or preserve unsupported visual descriptions."))
        if images:
            from ..inspection import image_context
            prompt += "\n" + image_context(images)
            model = settings.vision
        if include_tools:
            prompt += "\n" + self.registry.prompt_section()
        if choices:
            prompt += "\n" + CHOICES_HEADING + "\n" + id_lines(ids, "c")
        prompt += "\n" + answer_facts(runs, images=images, limitations=input_limits,
                                      targets=n_targets, outlined=n_outlined)
        prompt += ('\nWrite the answer yourself from these actual facts. Operation success alone '
                   'does not establish the original desired Goal or that its intended pixels appeared. '
                   'Pending work is not completed work. Do not replay actions. '
                   'Reply with JSON including answer, observation_scope and input_refs for this call'
                   + (', and ' + STATED if ids else '.'))
        if input_limit:
            prompt += ("\n" + input_limit + " The feed's availability is distinct from these supplied inputs. "
                       "Retain this unmet part in the answer; do not claim the whole comparison was fulfilled.")
        if getattr(ctx, "request_snapshot", None) is not None:
            from ..prompts import OBSERVATION_BASIS
            prompt += ("\nWorkspace structural facts above are the original Send pin, not a later UI observation. "
                       "Only completed receipts establish subsequent effects.\n" + OBSERVATION_BASIS)
        if not may_ask:
            # The three calls are spent: the answer keeps its own call once (admission.reserve_last_word), so the
            # user hears the model say what happened, never a bare receipt (live 2026-10-11 01:07).
            budget = getattr(ctx, "admission", None)
            reservation = budget.reserve_last_word() if budget is not None else None
            if reservation is None:
                return self._unavailable(ctx, "budget-exhausted", question, kept, ids)
        attempts = []
        try:
            if on_model_call is not None:
                on_model_call()
            if images or guard_ownership:
                from .toolbox.resources import require_current
                require_current(ctx, self.day)
            with capture_answer_attempts(attempts):
                result = await asyncio.wait_for(self._answer_model(model, [image for image, _ in images], prompt,
                    phase="final", reservation=reservation, max_output_tokens=REWRITE_TOKENS), timeout=PLANNER_TIMEOUT_S)
            if images or guard_ownership:
                require_current(ctx, self.day)
            from .claims import observation_limit
            limit = observation_limit(result.data or {}, images=images,
                                      require_scope=getattr(ctx, "request_snapshot", None) is not None)
            if limit:
                return self._unavailable(ctx, "unsupported-input", question, kept, ids, attempts)
            text = str((result.data or {}).get("answer") or "").strip()
            if text and progress and not any(r.ok is None for r in runs):
                return text  # the steps it called in progress finished while it was written: the caller writes again
            if text:
                return await self._said_everything(question, text, result.data or {}, ids, model, images, prompt,
                                                   ctx, attempts, named=(lambda words: model_problems(words, runs, also))
                                                   if runs else None)
            return self._unavailable(ctx, "empty-answer", question, kept, ids, attempts)
        except asyncio.CancelledError as exc:
            self._interpretation_failed(ctx, "final", "canceled", attempts, exc)
            raise
        except Exception as exc:
            reason = ("timeout" if isinstance(exc, asyncio.TimeoutError) else
                      "request-retired" if isinstance(exc, ToolFailed) else
                      "admission-denied" if isinstance(exc, AdmissionDenied) else "provider-error")
            return self._unavailable(ctx, reason, question, kept, ids, attempts, exc)
        return ""

    def _defaults_of_choices(self, runs) -> list[str]:
        """The default model of each role a choice of this request named (`use`, `set_default`), by name: an answer
        that says what is used otherwise names it from the settings, not from a step's receipt (stated_ids `also`)."""
        from ..operations import OPERATIONS
        from ..options import GROUPS

        out = []
        for run in runs:
            if run.tool not in ("use", "set_default") or run.ok is not True:
                continue
            role = str((run.args or {}).get("operation") or "").strip().lower()
            group = OPERATIONS[role].selected_by if role in OPERATIONS else role
            if group in GROUPS:
                option = GROUPS[group].get(object.__getattribute__(self.hub.settings, group))  # the default, not the choice
                if option is not None:
                    out.append(option.label)
        return out

    def _unavailable(self, ctx, reason: str, question: str, kept: str, ids: dict, attempts=(), error=None) -> str:
        """The answer written from the receipts could not be had. No prose, as always, except after a completion round
        that already answered these same settled receipts (`kept`): its answer, the model's grounded words, stands, and
        what it was not checked for is logged ("answer-recheck", unchecked). A retired request keeps no prose."""
        self._interpretation_failed(ctx, "final", reason, attempts, error)
        if not kept or reason == "request-retired":
            return ""
        log_override(self.agent.log, "answer-recheck", question=question, answer=kept, unchecked=list(ids), kept=True,
                     reason=reason)
        return kept

    async def _said_everything(self, question: str, text: str, data: dict, ids: dict, model: str, images, prompt: str,
                               ctx, attempts, named=None) -> str:
        """The answer states every problem it was told (ADR 0006): the reply names the ids it told the user about, and
        a passed-in problem it left out is a confirmed omission. Only then is it written once more, told exactly what it
        left out (admission.reserve_recheck: once per request, beyond the three). Still missing: the answer is kept, and
        the studio's "!" on it opens what happened (no app-written sentence, no error card: the user's rule of
        2026-10-11). Every decision is logged ("answer-recheck").
        `named`: the problems of the reply's own words (claims.model_problems: a model it names that no receipt names);
        one it was not already told is a confirmed problem too, with an id of its own (T4)."""
        known = set(ids.values())
        new = [p for p in (named(text) if named is not None else []) if p not in known]
        if new:
            count = sum(i.startswith("p") for i in ids)
            ids = {**ids, **{f"p{count + n}": p for n, p in enumerate(new, 1)}}
        if not ids:
            return without_ids(text, ids)
        if not new and not isinstance(data.get("stated"), list):
            # A reply without the list confirms nothing either way: kept, and logged as unchecked (never a guess).
            log_override(self.agent.log, "answer-recheck", question=question, answer=text, unchecked=list(ids), kept=True)
            return without_ids(text, ids)
        stated = {i for i in (data.get("stated") or []) if isinstance(i, str)} if isinstance(data.get("stated"), list) else set()
        missing = [i for i in ids if i not in stated or ids[i] in new]
        if not missing:
            return without_ids(text, ids)
        budget = getattr(ctx, "admission", None)
        reservation = budget.reserve_recheck() if budget is not None else None
        record = {"question": question, "answer": text, "missing": {i: ids[i] for i in missing}}
        if reservation is None:
            log_override(self.agent.log, "answer-recheck", **record, retried=False, kept=True)
            return without_ids(text, ids)
        again = (prompt + f'\nYour reply was: "{text}". It did not tell the user about: '
                 + "; ".join(f"{i} ({ids[i]})" for i in missing)
                 + ". Write the answer again so it does, in the same plain style, and list every id it tells about in stated.")
        try:
            with capture_answer_attempts(attempts):
                result = await asyncio.wait_for(self._answer_model(model, [image for image, _ in images], again,
                    phase="final", reservation=reservation, max_output_tokens=REWRITE_TOKENS), timeout=PLANNER_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the first answer stands; its "!" shows what did not work
            log_override(self.agent.log, "answer-recheck", **record, retried=True, kept=True, error=type(exc).__name__)
            return without_ids(text, ids)
        second = str((result.data or {}).get("answer") or "").strip()
        stated_again = result.data.get("stated") if isinstance(result.data, dict) else None
        still = [i for i in ids if not (isinstance(stated_again, list) and i in stated_again)]
        log_override(self.agent.log, "answer-recheck", **record, retried=True, kept=not second,
                     rewritten=second or None, stillMissing=still if second else missing)
        return without_ids(second or text, ids)

    def split_for_look(self, actions: list[tuple[str, dict]]) -> tuple[list, list]:
        """A fast decision's steps as (now, later): a highlight of things the running look saw
        needs a current look; while the slide has moved since, it waits for a new one (later).
        Everything else, and a highlight of what is already outlined, can be done now."""
        if self.sight_current():
            return list(actions), []
        scene = self.hub.scene.objects
        now, later = [], []
        deferred = set()
        for i, action in enumerate(actions, 1):
            name, args = action
            things = [str(t) for t in ((args or {}).get("things") or [(args or {}).get("thing")]) if t]
            after = getattr(action, "after_steps", None)
            waits = isinstance(after, list) and any(type(n) is int and n in deferred for n in after)
            if waits or (name == "highlight" and any(t not in scene for t in things)):
                later.append(action)
                deferred.add(action_number(action, i))
            else:
                now.append(action)
        return now, later

    def sight_current(self) -> bool:
        """The running look still describes the view (its s-ids can be pointed at)."""
        view = self.sight.view if self.sight is not None else None
        return view is not None and view.current(self.hub.latest_info)

    def _continuation_inputs(self, question, view, when_asked, request_snapshot, preceding):
        from dataclasses import replace
        from .claims import answer_facts

        moment = self.moment(when_asked, pushes=True, request_snapshot=request_snapshot)
        # These are current app identities, not coordinates on the held camera
        # picture. Do not overlay a newer board or Sight's boxes on older pixels.
        moment = replace(moment, outlined=moment.outlined_plain, seen="")
        notes = (self.registry.prompt_section(), *context.scene_table(moment))
        native = view[0] if view is not None and (getattr(view[0], 'source_image', None) or {}).get('nativeSource') else None
        question = results_followup(question, answer_facts(preceding, camera_pixels=view is not None and native is None,
            images=[(native.analysis_bytes, scientific_ask.facts(native))] if native is not None else ()))
        return question, moment, notes

    def _log_early_look(self, early: Look, channel: str, quick, used: bool) -> None:
        """What a typed request's look started beside the fast pass did (EARLY_LOOK_TYPED "alongside"): used, or
        dropped (and refunded once when it had entered the provider), with what the fast pass decided beside it."""
        try:
            self.agent.log({"kind": "early-look", "channel": channel, "question": early.words, "used": used,
                            "entered": bool(early.attempt_ids), "forgiven": early.forgiven,
                            "fast": None if quick is None else {"needsLook": bool(quick.needs_look),
                                                                "actions": len(quick.actions), "ms": round(quick.ms)},
                            "sinceStartMs": round((time.perf_counter() - early.started) * 1000)})
        except Exception:
            pass  # the log never changes the request

    def start_look(self, question: str, frame_id: int | None = None, when_asked=None, *, request_snapshot=None, preceding=()) -> Look:
        """Start the vision call for a request now, before it is confirmed (hands, W2-1); the
        same call Brain.turn would make, on the same notes. Raises tools.ToolError without a
        fresh picture. A trusted caller can supply the original pin explicitly; it is
        validated against the current admission before any dispatch."""
        from ..ticket import current
        from ..capture import RUN
        from ..workspace_observation import ObservationError

        request = current()
        if request_snapshot is not None:
            if request is None or request.admission is None or request.closed or request.sealed:
                raise ObservationError("INVALID_REPORT", "a scoped look needs its original active admission")
            screen = self.hub.screen
            _origin, profile = screen._origin(request_snapshot)
            expected = screen._snapshot_id(profile, request.admission.request_id,
                                           request_snapshot.observation, request_snapshot.timing)
            if profile.app_run_id != RUN or request_snapshot.snapshot_id != expected:
                raise ObservationError("CLIENT_MISMATCH", "look pin belongs to another run/admission")
        settings = self.hub.settings
        view = self.agent.snapshot(frame_id)
        moment = self.moment(when_asked, pushes=True, request=question, request_snapshot=request_snapshot)
        table, selected = context.scene_table(moment)
        tool_text = (self.registry.prompt_section(), table, selected)
        planning_question = question
        if preceding:
            planning_question, moment, tool_text = self._continuation_inputs(
                question, view, when_asked, request_snapshot, preceding)
        pointing = "box" if settings.pointing == "snap-first" else settings.pointing
        attempt_ids = []

        async def inspect():
            with capture_answer_attempts(attempt_ids):
                return await self._look(planning_question, view, option=settings.vision, pointing=pointing, notes=tool_text,
                                        mode=settings.brain, registry=self.registry)

        task = asyncio.create_task(inspect())
        if request is not None:
            request.track(task)
        request_id = request.admission.request_id if request is not None and request.admission is not None else ""
        return Look(question, view, moment, tool_text, task, time.perf_counter(), request_id=request_id,
                    attempt_ids=attempt_ids, log=getattr(self.agent, "log", None),
                    admission=request.admission if request is not None else None)

    async def quick(self, question: str, followup: bool = False, point_only: bool = False, moment: Moment | None = None,
                    when_asked=None, model=None):
        """The fast pass (brain/fast.py) on the notes (`moment`, else now, as of `when_asked`), or None if it
        fails or is slow (then the vision call answers)."""
        from .fast import decide

        model = model or self.hub.settings.fast
        from ..ticket import current

        try:
            phase = "query" if followup else "hands" if current() is not None and current().kind == "hands" else "fast"
            with answer_phase(phase):
                return await asyncio.wait_for(decide(moment or self.moment(when_asked), question, self.agent.vision(model), self.registry.prompt_section(),
                                                     model, followup=followup, point_only=point_only), timeout=PLANNER_TIMEOUT_S)
        except CallRefused as exc:
            if not exc.retry and not followup and not point_only:
                # The example's local-only policy or the monthly cap: the full look would be refused the same way, so
                # the request says this one's words. A follow-up round keeps the answer it already has.
                raise
            _QUICK_REFUSED.set(exc)
            return self._quick_fell_back(exc, model, followup)
        except Exception as exc:
            return self._quick_fell_back(exc, model, followup)

    def _quick_fell_back(self, exc: BaseException, model, followup: bool):
        # The quick decision did not come: the full look answers in its place. Said, with why (fallbacks.py; the
        # cut-off itself is kept with its reason by the provider call's own record, api_usage.attempt).
        from .. import fallbacks
        from ..options import VISION

        why = (f"no answer within {PLANNER_TIMEOUT_S:.0f} s" if isinstance(exc, TimeoutError)
               else f"{type(exc).__name__}: {str(exc)[:100]}")
        label = VISION[model].label if model in VISION else str(model)
        instead = "the answer as written, no follow-up round" if followup else "the full look"
        fallbacks.of(self.hub).note("quick decisions", label, instead, why)
        log = getattr(self.agent, "log", None)
        if callable(log):
            log({"kind": "fallback", "role": "fast", "phase": "query" if followup else "decide", "default": model,
                 "used": "no follow-up" if followup else "look", "why": why})
        return None

    async def reflex(self, question: str, moment: Moment | None = None, when_asked=None):
        """The pointing reflex (option REFLEX "lean..."; brain/fast.py LEAN_POINT): which listed ids a spoken
        question is about, from the words, the eyes and the board only; None if it fails or is slow."""
        from .fast import lean
        from ..options import REFLEX_MODELS

        option = getattr(self.hub.settings, "reflex", "after-voice")
        model = REFLEX_MODELS.get(option) or self.hub.settings.fast
        try:
            # A specialist call, as the outline check is: it never spends the request's own model calls (run beside
            # hands and the look, it took the look's room: "final-reserved", acting case live-163622).
            return await asyncio.wait_for(lean(moment or self.moment(when_asked), question, self.agent.vision(model), model),
                                          timeout=REFLEX_TIMEOUT_S)
        except Exception as exc:
            log = getattr(self.agent, "log", None)
            if callable(log):
                log({"kind": "point", "channel": "voice", "question": question, "actions": [], "reflex": option,
                     "error": f"{type(exc).__name__}: {str(exc)[:100]}"})
            return None

    async def run_actions(self, actions: list[tuple[str, dict]], ctx: ToolContext,
                          numbers: list[int] | None = None) -> list[ToolRun]:
        runs = await ActionRunner(self.registry, self.hub, lambda: self.sight, self._late, self.board,
                                  log=getattr(self.agent, "log", None)).run(actions, ctx, numbers)
        for r in runs:  # a verdict on a first guess, landed within the wait, is remembered on every path (M2.1 re-check)
            remembered = self._remember_run_outputs(r, ctx)
            if r.tool == "highlight" and r.ok and (r.args or {}).get("checks") and not remembered:
                self.conversation.add("note", ctx.channel, f"highlight finished: {r.summary}", asked=ctx.asked)
        return runs

    def _remember_run_outputs(self, run: ToolRun, ctx: ToolContext, *, unbound=False) -> bool:
        """One original action's context receipt in the existing conversation journal.

        Completion facts precede model interpretation. The immutable producer
        outputs qualify source birth; neither today's board nor a prose reply
        can create or refresh them. The journal itself deduplicates carries by
        the original occurrence, without a second receipt store.
        """
        store, asked = self.conversation.store, ctx.asked
        if (run.ok is not True or asked is None or asked.routing_generation != store.generation
                or asked.thread not in store.threads or (asked.day is not None and asked.day != store.sid)):
            return False
        if not unbound and not (type(ctx.turn_id) is int and ctx.turn_id > 0):
            return False  # inline Hands can bind its own user turn at the handled handoff
        occurrence = run.occurrence
        if occurrence is not None and any(
                turn.role == "note" and (turn.refs or {}).get("requestId") == occurrence.request_id
                and (turn.refs or {}).get("actionId") == occurrence.action_id
                for turn in store.threads[asked.thread].turns):
            return True
        outputs = []
        if run.outputs:
            # Hands may publish before its handled handoff binds the user turn.
            # Only that same original occurrence/context can fill missing receipt
            # association; the producer's immutable source facts stay unchanged.
            origin_turn = None
            if (type(ctx.turn_id) is int and ctx.turn_id > 0 and ctx.asked is not None
                    and self.day is not None and ctx.asked.routing_generation == self.day.generation
                    and run.occurrence is not None and ctx.admission is not None
                    and run.occurrence.request_id == ctx.admission.request_id and run.step is not None):
                number, steps = run.step
                if (steps.runs.get(number) is run and steps.occurrences.get(number) == run.occurrence
                        and steps.outputs.get(number) is run.outputs):
                    origin_turn = ctx.turn_id
            for output in run.outputs:
                item = output.wire()
                if item.get("kind") == "outline":
                    receipt = {key: item.get(key) for key in
                               ("id", "run", "frame", "epoch", "created_at", "turn_id")}
                    if receipt["turn_id"] is None:
                        receipt["turn_id"] = origin_turn
                    outputs.append(receipt)
        # These are the successful original occurrence's frozen outputs, not all
        # outlines accumulated in ctx or a lookup against a later board. Supplying
        # qualified refs directly also preserves a source whose outline is gone.
        if not outputs and run.resource is None:
            return False
        self.conversation.add("note", ctx.channel, f"{run.tool} finished: {run.summary}",
                              objectIds=list(dict.fromkeys(row["id"] for row in outputs)),
                              resourceIds=[run.resource["id"]] if run.resource else [], asked=asked,
                              outputRefs=outputs,
                              requestId=occurrence.request_id if occurrence is not None else None,
                              actionId=occurrence.action_id if occurrence is not None else None)
        return True

    async def _late(self, run: ToolRun, ctx: ToolContext) -> None:
        """A tool that finished after the answer: tell the user on the channel they asked on."""
        if not self._remember_run_outputs(run, ctx, unbound=True):
            self.conversation.add("note", ctx.channel, f"{run.tool} finished: {run.summary}",
                                  resourceIds=[run.resource["id"]] if run.resource else [], asked=ctx.asked,
                                  outputRefs=[])
        if self.report_late is not None:
            await self.report_late(ctx, run)

    async def note_to_voice(self, text: str) -> None:
        """Silent context for the voice about what happened in chat. It goes out with the next
        view note, as one context update (GPT-Live reads a plain note aloud, and two notes
        close together sometimes set it talking; worded as context only it stays silent:
        docs/bench/spike-s2-gpt-live.md)."""
        # Kept while the voice is off too: the next voice session gets them in its recap.
        self.voice_notes.append(text)
        del self.voice_notes[:-12]


def step_key(tool: str, args: dict, aliases: dict) -> tuple:
    """The same requested effect only: normalized arguments keep scope, controls and selectors distinct.
    A seen reference is its request's promoted outline; labels use the resolver's case/whitespace rules."""
    def normalized(value):
        if isinstance(value, str):
            text = " ".join(value.strip().lower().split())
            return str(aliases.get(value.strip(), aliases.get(text, text))).lower()
        if isinstance(value, dict):
            return tuple(sorted((k, normalized(v)) for k, v in value.items()))
        if isinstance(value, (list, tuple)):
            return tuple(normalized(v) for v in value)
        return value

    effect = dict(args or {})
    if tool == "clear":
        effect.setdefault("what", "all")  # the clear tool's actual omitted scope
    if tool == "zoom" and effect.get("hold") is False:
        effect.pop("hold")
    if tool == "compare" and "a" in effect and "b" in effect:
        a, b = normalized(effect.pop("a")), normalized(effect.pop("b"))
        effect["pair"] = tuple(sorted((a, b), key=str))  # comparison order does not change the requested pair
    return tool, normalized(effect)


def repeated_occurrences(steps: list[tuple[str, dict]], done: list[ToolRun], aliases: dict) -> dict:
    """Match each carried run at most once, retaining its original step/output identity for @n.
    No name-only or target-only merge: distinct effects on the same thing still run."""
    left = [(step_key(r.tool, r.args, aliases), r) for r in done]
    out = {}
    for compact, action in enumerate(steps, start=1):
        name, args = action
        i = action_number(action, compact)
        key = step_key(name, args, aliases)
        match = next((j for j, (earlier, run) in enumerate(left) if earlier == key
                      and (not dependent(action) or (run.step and run.step[0] == i
                           and run.after_steps == action.after_steps))), None)
        if match is not None:
            _, run = left.pop(match)
            out[i] = run.step or 0
    return out


def repeated_steps(steps: list[tuple[str, dict]], done: list[ToolRun], aliases: dict) -> set[int]:
    """Numbers of repeated steps (the occurrence map also carries their outputs in the real loop)."""
    return set(repeated_occurrences(steps, done, aliases))


def first_guess_targets(actions: list[tuple[str, dict]]) -> list[int]:
    """The answer's targets (1-based) a step checks by measuring ("highlight ... checks: target 1")."""
    from ..board import TARGET

    out = []
    for name, args in actions:
        m = TARGET.fullmatch(str((args or {}).get("checks") or "").strip()) if name == "highlight" else None
        if m and int(m.group(1)) not in out:
            out.append(int(m.group(1)))
    return out


async def mark_first_guesses(hub, targets: list[int], ctx: ToolContext) -> None:
    """Each of those targets this request's look outlined is marked a first guess in code (M2.1 review B3):
    a "?" on its label and a note saying it is being measured. Never one the user named or confirmed."""
    from ..apply import apply
    from .toolbox.view import made_by_this_request, named_since

    for n in targets:
        listed = ctx.targets or ctx.objects  # by the look's own numbers when it pointed (re-check SF2)
        oid = listed[n - 1] if 0 < n <= len(listed) else None
        obj = hub.scene.objects.get(oid) if oid else None
        if obj is None or obj.status == "confirmed" or not made_by_this_request(obj) or named_since(obj):
            continue  # never one the user named, confirmed or renamed
        label = obj.label if not obj.label or obj.label.endswith("?") else f"{obj.label}?"
        note = "a first guess by eye, being measured" + (f"; {obj.note}" if obj.note else "")
        await apply(hub, lambda _, o=obj, lb=label, nt=note: hub.scene.update(o.id, label=lb, note=nt),
                    scope="outline:app", target=(obj.id, None))
