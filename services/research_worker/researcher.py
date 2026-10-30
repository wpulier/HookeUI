"""One researcher: its tools (gated by effort, in code), its budget and its files.

The engine (engines.py) talks to the model; every tool call comes here. Files go to the
session directory the orchestrator made:
  inputs/frame.jpg, inputs/crop.jpg      the snapshot it started from
  steps.jsonl                            what it did, one line per step
(and, outside it, in the performance logs, its conversation with the model round by round: transcript.py)
  outputs/NN-slug.png                    annotated / generated pictures
  sources/<sha1>.jpg, sources.json       reference pictures it fetched (uncited ones are
                                         deleted when it finishes)

Each output is also emitted with readable `content` (results.py): a lesson's questions and
answers, an annotated picture's labels and where they point, an illustration's prompt. When
time or money runs out after something was made, the work is kept as a "partial" result.
What needs no network (prompts, edits, label changes, schemas, result text) is in work.py.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import base64
import io
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from orchestrator.untrusted import block

from . import claims as claim_check
from . import evidence as evidence_check
from . import transcript
from .limits import FIELD_MAX, HOLD_MAX_S, TooLong, whole
from .api_usage import refusal, refusal_clause
from . import video
from .budget import (ALIVE_EVERY_S, CALL_MOST, CELL_FINDERS, CHART_TOOLS, CHECK_IN_COST_X, CHECK_IN_S, CHECK_IN_WAIT_S, EFFORTS, PRICES,
                     RUN_OPERATIONS, RUN_WAIT_MAX_S, STALL_ROUNDS, WRAP_UP_AT,
                     Budget, OverBudget, estimate_cancelled, image_cost, operation_limit, tokens_cost)
from .fidelity import ALTERED_ABOVE, alteration, square_pad
from .lesson_check import (CHECK_ESTIMATE_USD, CHECK_MODEL, REWRITE_ESTIMATE_USD, blind_prompt, change_text, grade_prompt,
                           rewrite_prompt, right_ids)
from .prompts import PLAN, RULES, SYSTEM, deliverable_text
from .results import LESSON_MAX, labels_content, lesson_content, partial_result, step_kind, text_content
from .provenance import COMPOSITE, GENERATED, ILLUSTRATION, LABELLED, mark_png, mark_video
from .safe_fetch import FetchRefused, fetch_image, fetch_text, page_images
from .session_index import IndexUnavailable, NoEntry, NoPicture, SessionIndex, capped, newest_first
from .work import (LESSON, UnreadableReply, REVISE_LABELS, REVISE_LESSON, SCHEMAS, clean_lesson, draw_labels, edited_lesson, editing_prompt,
                   inline_photo, json_reply, named_steps, offline_lesson, slug, trim)

IMAGE_MODEL_DEFAULT = "gpt-image-2.5-flare"
# The picture models' one names (catalog/models/<id>/model.toml `name`; a test keeps them the same), said when one can't be
# used: the research process does not import the app.
IMAGE_NAMES = {"gpt-image-2.5-flare": "GPT Image 2.5 Flare", "gpt-image-2.5-sunburst": "GPT Image 2.5 Sunburst",
               "gemini-3.1-flash-image": "Gemini 3.1 Flash Image"}
VIDEO_MODEL = video.MODEL  # research_worker/video.py: Veo 3.1 Fast's successor since Google retired it
VIDEO_NAME = video.NAME  # its one name (catalog/models/gemini-omni-flash), said when it can't be used
CHART_TABLE_MAX = 2_000_000  # characters of one table a chart reads whole (measurement_charts.py)
LESSON_CLAUDE = "claude-sonnet-5-5"  # lessons when ANTHROPIC_API_KEY is set (capability role fixed:lesson-writer)
LESSON_FALLBACK = "gpt-6.1-sol"  # lessons otherwise


def _as_jpeg(body: bytes) -> bytes | None:
    """A picture from the session's index as JPEG (the engines attach JPEG): PNG snapshots are re-encoded."""
    if body[:2] == b"\xff\xd8":
        return body
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    ok, out = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return out.tobytes() if ok else None


STEP_MARKS = {"find": ", find on your slide", "evidence": ", evidence"}  # how the page writer is told a step's kind
MAX_REQUESTS = 2  # evidence a researcher may ask the user for in one run (request_evidence)
# A question to the user keeps its asker waiting for the answer (the end of the effort's kind (h): "a researcher asked
# 'yes or no' and finished before the yes"): a finish while one of this run's questions is unanswered, or footage the
# user agreed to is still being held, is held this long for it, once each, when time and a round are left for it.
ANSWER_WAIT_S = 30.0
ROUND_RESERVE_S = 15.0  # a round's time: an effort shorter than this and an answer's wait finishes before an answer could be read
# Session entries kept whole to chart or capture from (charts.resolve): at most this many, each at most this long
# (the same as a researcher's handoff inputs: manager.start).
DOCUMENT_MAX, DOCUMENTS_MAX = 240000, 6
FETCH_TRIES = 2  # tries of one address whose fetch failed for a reason that may pass (a timeout, HTTP 429 or 5xx)
LABELS_MAX = 6  # labels that fit on one picture (annotate_image refuses more, saying so)
LABEL_NAME_MAX = 40  # characters of a label drawn on a picture



# What a run with no kept input works on, said before the app answers: the Stage may show the live camera or a still
# picture, and this process can't tell which, so it names the Stage (the app's reply names which: research/hands.py).
STAGE = "the picture on the Stage"


def said_words(words) -> str:
    """The app's words about a run, for the card: the ids it names for the researcher (e3, m2, k1...) left out."""
    import re as _re

    text = str(words or "").strip()
    text = _re.sub(r",\s*[a-z]\d+(?=\))", "", text)  # "(Cell outlines · 18:04, e7)" -> "(Cell outlines · 18:04)"
    text = _re.sub(r"\s*\((?:[a-z]\d+(?:,\s*)?)+\)", "", text)  # "(e3)", "(m2, m3)" -> ""
    return _re.sub(r"\b(?:entry|population|item)\s+[a-z]\d+\b", "it", text)

def provider_status(error) -> str:
    """A provider's refusal or failure in a few plain words with its own status ("HTTP 404, NOT_FOUND: models/... is not
    found"), from the error itself (its HTTP code, its status name and message; an operation's error dict), never a
    guess. Addresses are left out: the words are said on the researcher's card."""
    import os

    from .api_usage import OUTCOMES, outcome_of

    if isinstance(error, dict):
        code, name, message = error.get("code"), error.get("status"), error.get("message")
        words = ""
    else:
        if isinstance(error, ValueError) and not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            return "no Gemini key is set here"
        code = getattr(error, "code", None) or getattr(error, "status_code", None)
        name, message = getattr(error, "status", None), getattr(error, "message", None) or str(error)
        outcome = outcome_of(error)
        words = OUTCOMES[outcome] if outcome != "failed" else ""
    head = ", ".join(str(x) for x in (f"HTTP {code}" if isinstance(code, int) and code >= 100 else "",
                                      name if isinstance(name, str) else "", words) if x)
    text = re.sub(r"https?://\S+", "", " ".join(str(message or "").split()))[:160].strip(" .,;:")
    return ": ".join(x for x in (head, text) if x) or "it failed without saying why"


def said(exc: Exception) -> str:
    """The words of an exception the app writes in plain words for the user (OverBudget, RunFailed): only these are
    said on a step's text; any other exception goes in its detail (tests/test_research_progress_words.py)."""
    return str(exc)


class RunFailed(Exception):
    """A run that cannot go on, for a reason said plainly (an engine's empty answer): it ends "failed" with that
    reason as its last step, not a traceback."""


def lesson_writer() -> str:
    """The model that writes a lesson's page: Claude with an Anthropic key, else the fallback (write_lesson)."""
    import os

    return LESSON_CLAUDE if os.environ.get("ANTHROPIC_API_KEY") else LESSON_FALLBACK


LESSON_MAX_OUTPUT = 16_000
PAGE_MAX_CHARS = 400_000  # a researcher's own HTML page (make_page): slides or a small visual, its data inline
EDIT_MAX_OUTPUT = 8_000
CHECK_MAX_OUTPUT = 4_000


async def write_lesson(prompt: str, researcher, out: int | None = None) -> tuple[str, str]:
    """The lesson's page; `out`: a limited run's output limit (Researcher.bound), sent as the provider's own limit.
    Its caller counts it should it end with no usage (Researcher.sent)."""
    import os

    writer = lesson_writer()
    if writer == LESSON_CLAUDE:  # untested until a key is added; the same prompt and checks apply
        import httpx

        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post("https://api.anthropic.com/v1/messages", headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": LESSON_CLAUDE, "max_tokens": out or LESSON_MAX_OUTPUT,
                      "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            data = r.json()
        usage = data.get("usage") or {}
        researcher.spend(LESSON_CLAUDE, tokens_cost(LESSON_CLAUDE, usage.get("input_tokens", 0), usage.get("output_tokens", 0)))
        return "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text"), LESSON_CLAUDE
    import openai

    # No hidden SDK retries: the one attempt is the one admitted (a limited run's most funds one call, not three).
    response = await openai.AsyncOpenAI(max_retries=0).responses.create(model=LESSON_FALLBACK, input=prompt, reasoning={"effort": "medium"},
                                                           **({"max_output_tokens": out} if out else {}))
    usage = response.usage
    researcher.spend(LESSON_FALLBACK, tokens_cost(LESSON_FALLBACK, getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0)))
    return response.output_text or "", LESSON_FALLBACK



EDIT_MODEL = "gpt-6.1-sol"  # every small edit, whatever the Researching model (capability role fixed:editor)


def engine_name(engine) -> str:
    """The engine that actually ran (reported with the result, so the app never records the setting instead)."""
    return str(getattr(engine, "model", None) or type(engine).__name__)


async def edit_json(prompt: str, researcher, image: bytes | None = None) -> dict:
    """One small edit call (a lesson's content or a picture's labels) returning JSON; `image`: a
    photo it sees with the prompt (a lesson's steps rewritten to need it, lesson_check.py)."""
    import openai

    request = prompt if image is None else [{"role": "user", "content": [
        {"type": "input_text", "text": prompt},
        {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(image).decode(), "detail": "high"}]}]
    pictures = 0 if image is None else 1
    out, most = researcher.bound(EDIT_MODEL, prompt, pictures=pictures, ceiling=EDIT_MAX_OUTPUT)
    async with researcher.sent(EDIT_MODEL, prompt, pictures=pictures, most=most):
        response = await openai.AsyncOpenAI(max_retries=0).responses.create(model=EDIT_MODEL, input=request, reasoning={"effort": "low"},
                                                               text={"format": {"type": "json_object"}},
                                                               **({"max_output_tokens": out} if out else {}))
    usage = response.usage
    researcher.spend(EDIT_MODEL, tokens_cost(EDIT_MODEL, getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0)))
    return json_reply(response.output_text, strict=True)


async def fast_json(prompt: str, researcher) -> dict:
    """One call to the cheap check model (lesson_check.CHECK_MODEL), text only, returning JSON."""
    from google import genai
    from google.genai import types

    out, most = researcher.bound(CHECK_MODEL, prompt, ceiling=CHECK_MAX_OUTPUT)
    async with researcher.sent(CHECK_MODEL, prompt, most=most):
        response = await genai.Client(http_options={"retry_options": {"attempts": 1}}).aio.models.generate_content(
            model=CHECK_MODEL, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", max_output_tokens=out,
                                               thinking_config=types.ThinkingConfig(thinking_level="low")))
    usage = response.usage_metadata
    researcher.spend(CHECK_MODEL, tokens_cost(CHECK_MODEL, getattr(usage, "prompt_token_count", 0) or 0,
                                              (getattr(usage, "candidates_token_count", 0) or 0)
                                              + (getattr(usage, "thoughts_token_count", 0) or 0)))
    return json_reply(response.text, strict=True)


class Researcher:
    def __init__(self, spec: dict, emit: Callable[[dict], None], request: Callable[..., Any]) -> None:
        self.spec = spec
        self.rid = spec["rid"]
        self.dir = Path(spec["dir"])
        self.effort = EFFORTS[spec.get("effort") or "instant"]
        self.budget = Budget(self.effort, unlimited=bool(spec.get("unlimited_budget")) and self.effort.name != "build")
        self.emit_raw = emit
        self.request = request
        self.result: dict | None = None
        self.lesson_draft: dict | None = None
        self.response_id: str | None = spec.get("previous_response_id")
        self.search_sources: list[str] = []  # pages its searches returned, as given (shown with the result)
        # A search's redirect address -> the site it stands for (Gemini's grounding gives a new redirect for the same
        # page in every response, engines.searched): progress counts the site once, never the redirect.
        self.source_names: dict[str, str] = {}
        self.refs: dict[str, dict] = {}  # url asked for -> saved reference
        self.saved_finding_refs: set[str] = set()  # scientific revisions survive a later presentation failure
        self.outputs = 0
        self.made: list[dict] = []  # this run's outputs, as emitted (for a partial result)
        # Models this run could not use, each with why and what to do (api_usage.refusal): its result says them as facts
        # (`unavailable`), whatever its write-up says (the live partner spec's 4.8, horizontal 4).
        self.unavailable: list[dict] = []
        self.failed_urls: dict[str, str] = {}  # url -> why it could not be fetched (never fetched again)
        self.fetch_tries: dict[str, int] = {}  # url -> fetches that failed for a reason that may pass (FETCH_TRIES)
        self.spent_estimated = 0.0  # calls cancelled by a stop (billed, usage unknown)
        self.offline = False  # the scripted engine: pictures and lessons are made locally
        self.started_at = time.monotonic()
        self.last_snapshot = 0.0
        self.runs = 0  # the app's operations it asked for in this run (run; the app checks it too)
        self.app_results: set[tuple[str, str, str]] = set()  # actual distinct (day, operation, entry) this worker received
        self.crop_jpeg = self._read("inputs/crop.jpg")
        self.frame_jpeg = self._read("inputs/frame.jpg")
        self.revising: dict | None = spec.get("revise")  # {"kind", "change", "target": the output to make a new version of}
        self.inbox_seen = int(spec.get("inbox_from") or 0)  # the user's notes already read (earlier runs)
        self.inbox_from = self.inbox_seen  # where this run's notes start (an earlier run's answers are not this run's)
        self.round_n = 0  # the round it is in (stalled, called once at the start of each)
        self.waited: set[str] = set()  # questions and holds a finish already waited for (once each)
        self.notes_heard: list[str] = []
        self.session_index = None  # the session's one index, read-only, when the app gave its address
        self.session_index_error = ""  # why an address the app gave could not be used (said, never silent)
        if spec.get("resources_url"):
            try:
                # Its own session's index (an older app's spec names only its day: the session of that date).
                self.session_index = SessionIndex(str(spec["resources_url"]), spec.get("session") or spec.get("day"))
            except ValueError as exc:
                self.session_index, self.session_index_error = None, str(exc)
        self.session_read: set[str] = set()  # entries it read in full (new to work on: progress)
        self.session_pages: dict[tuple[str, str], int] = {}  # next contiguous offset, per immutable reading
        self.session_page_reads: set[tuple[str, str, int]] = set()
        self.session_documents: dict[tuple[str, str], dict] = {}
        self._document_parts: dict[tuple[str, str], str] = {}
        self._looked_at: set = set()  # (entry, how) it looked at in the session: emitted once each
        self.session_seen: dict[str, list[int]] = {}  # every version of each entry it listed or read (evidence.py)
        self.requests_made = 0  # evidence it asked the user for this run (request_evidence, at most MAX_REQUESTS)
        # Its questions are numbered on from those of its earlier runs, as the app numbers them (one numbering: the
        # app's answer marks name the same ids; #194 review B1).
        self.requests_from = int(spec.get("requests_from") or 0)
        self.plan: dict | None = None  # what a deep run said it would look up first (tool_plan)
        self.final_round = False  # its last chance to finish (engines._final): finish keeps what it can
        self.told_write_up = self.told_nearly_up = False  # said once each (engines._final)
        self.forced_write_up: str | None = None  # why it must write up now (no answer to "keep going?")
        self.ran_facts: list[dict] = []  # each operation it had the app run, with the app's own facts: its work too
        self.asked = 0  # questions it asked the user this run (evidence and "keep going?"), numbered on from earlier runs
        self.evidence_ids: list[str] = []  # the evidence questions among them (a finish waits a little for these)
        self.next_check_at = CHECK_IN_S  # its running seconds at which it next asks "keep going?"
        self.next_check_cost = CHECK_IN_COST_X * self.effort.cap_usd  # and its spend (with no daily cap)
        self.rejected: list[dict] = []  # parts of a finish the app could not accept: what was sent and why
        self.recorded: list[dict] = []  # its record_finding calls, whole ({kind, text, sources})
        self.without_finish = False  # it answered in plain text instead of calling finish
        self._progress = 0  # how much it had found when it last found something new (stalled)
        # The builder's own work, each distinct: a source file read, a draft by its content, a file pinned, a try's outcome
        # (acceptance 2026-10-14: four new files read in three rounds counted as nothing, and the stall rule ended it before
        # it drafted). The same draft or try again is nothing new.
        self.builder_work: set = set()
        self._progress_round = 0
        for sub in ("outputs", "sources"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)
        self.outputs = len(list((self.dir / "outputs").iterdir()))  # a follow-up adds after the earlier outputs
        if self.session_index_error:
            self.step("Today's session can't be read here; it works from the web and its own notes", "warning",
                      detail=str(self.session_index_error))

    # --- plumbing -------------------------------------------------------------------
    def _read(self, rel: str) -> bytes | None:
        path = self.dir / rel
        return path.read_bytes() if path.exists() else None

    def emit(self, event: str, **data) -> None:
        self.event_sequence = getattr(self, 'event_sequence', 0) + 1
        self.emit_raw({"event": event, "rid": self.rid, **data,
                       **({'runId': self.spec['run_id'], 'eventSequence': self.event_sequence} if self.spec.get('run_id') else {}),
                       **({'receiptId': uuid.uuid4().hex} if event == 'cost' else {})})

    def step(self, text: str, kind: str = "step", detail: str | None = None) -> None:
        """One step of its progress. `text` is what the user sees on its card and chip, in plain words: never an
        entry's id, a character range, an exception's name, a file or a URL (2026-10-11: "Read d1: characters
        0–4233"); those go in `detail`, kept with the step for Activity's details and How it was made
        (tests/test_research_progress_words.py checks every call)."""
        record = {"at": round(time.time(), 2), "kind": kind, "text": text if len(text) <= 300 else text[:299] + "…"}
        if detail:
            record["detail"] = detail if len(detail) <= 600 else detail[:599] + "…"
        with (self.dir / "steps.jsonl").open("a") as f:
            f.write(json.dumps(record) + "\n")
        self.emit("step", text=record["text"], kind=kind, **({"detail": record["detail"]} if detail else {}))

    def transcribe(self, asked: dict, reply: dict | Callable[[], dict], instructions: str | None = None) -> None:
        """One round of its conversation with the model, appended to its transcript (transcript.py; the app names the
        file, spec "transcript"): `asked` what the engine sent this round (`sent`, the tools offered, how it was asked
        to choose), `reply` the model's whole answer or the call's error (or what makes it, read here, so a reply that
        can't be read costs the log line, never the run). The instructions only by their hash, and only in a round that
        sent them (later rounds of a response chain carry them)."""
        if not self.spec.get("transcript"):
            return
        try:
            record = {"rid": self.rid, "runId": self.spec.get("run_id"), "effort": self.effort.name, **asked,
                      "instructions": ({"sha256": transcript.sha(instructions), "chars": len(instructions)}
                                       if instructions else None),
                      "sent": transcript.plain(asked.get("sent")), "reply": transcript.plain(reply() if callable(reply) else reply)}
        except Exception as exc:  # noqa: BLE001 - a log never fails a run
            record = {"rid": self.rid, "round": asked.get("round"), "unreadable": f"{type(exc).__name__}: {exc}"[:300]}
        transcript.append(self.spec.get("transcript"), record)

    def spend(self, what: str, usd: float) -> None:
        self.budget.add(what, usd)
        self.emit("cost", spent=round(self.budget.spent, 4), estimated=round(self.spent_estimated, 4),
                  charge={"model": what, "usd": usd, "basis": "Research estimate"})

    def committed(self) -> float:
        """What the run has spent: reported usage, and calls sent without usage (cut off, failed), each at what it may
        have cost (under a limit, its most)."""
        return self.budget.spent + self.spent_estimated

    def bound(self, model: str, text: str, pictures: int = 0, ceiling: int = 8_000) -> tuple[int | None, float | None]:
        """(its output limit, its most) for one of this run's calls, sized to what is left under a limit (OverBudget when
        nothing fits); (None, None) with no limit: sent as before."""
        if self.budget.unlimited:
            return None, None
        from .budget import OVERHEAD_TOKENS, PER_PICTURE_TOKENS, bounded_call

        return bounded_call(self.budget, self.committed(), model,
                            len(text.encode()) + pictures * PER_PICTURE_TOKENS + OVERHEAD_TOKENS, ceiling)

    @contextlib.asynccontextmanager
    async def sent(self, model: str, text: str, pictures: int = 0, most: float | None = None):
        """One call sent: should it end with no usage (cut off, failed), it is counted at its most under a limit
        (it may have been billed), else estimated as before, then the error goes on."""
        try:
            yield
        except asyncio.CancelledError:
            self.cancelled_call(model, text_chars=len(text), images=pictures, usd=most)
            raise
        except Exception:
            if most is not None:
                self.cancelled_call(model, text_chars=len(text), images=pictures, usd=most, uncertain=True)
            raise

    def cancelled_call(self, model: str, text_chars: int = 0, images: int = 0, search: str | None = None,
                       usd: float | None = None, uncertain: bool = False) -> None:
        """A model call was cancelled (a stop) after it was sent: it is billed
        though no usage came back, so an estimate is counted (budget.estimate_cancelled).
        `uncertain`: a call that failed (a timeout, a server error) may or may not have been billed;
        the estimate is counted all the same, and said to be uncertain."""
        cost = usd if usd is not None else estimate_cancelled(model, text_chars, images, search)
        self.spent_estimated += cost
        self.emit("cost", spent=round(self.budget.spent, 4), estimated=round(self.spent_estimated, 4),
                  charge={"model": model, "usd": cost, "basis": "Failed call estimate, usage uncertain" if uncertain
                          else "Interrupted call estimate", **({"uncertain": True} if uncertain else {})})

    def wrap_up_due(self) -> bool:
        """Most of its usual time is gone: it is told to plan its write-up (never made to stop by it)."""
        return time.monotonic() - self.started_at >= WRAP_UP_AT * self.effort.seconds

    def write_up_due(self, round_n: int) -> bool:
        """Its effort's time or rounds are used: it is told to write up now (engines.WRITE_UP_NOW), every round after.
        A round started before this point and still running past it is never cut."""
        return time.monotonic() - self.started_at >= self.effort.seconds or round_n > self.effort.max_rounds

    async def keep_going(self) -> None:
        """The loop guard the user chose (budget.CHECK_IN_S): after an hour of running, and when its spend passes
        CHECK_IN_COST_X times its effort's usual cost with no daily cap set, it asks the user whether to keep going and
        pauses (no model call, nothing spent) until they answer. Their answer is its next note, and the model decides
        from it; no answer in CHECK_IN_WAIT_S makes it write up what it has (engines._final)."""
        if (self.forced_write_up or self.spec.get("purpose") == "description"
                or self.spec.get("deliverable") in ("answer", "revise")):
            return
        ran, spent = time.monotonic() - self.started_at, self.budget.spent
        over_time = ran >= self.next_check_at
        over_cost = self.budget.unlimited and spent >= self.next_check_cost
        if not (over_time or over_cost):
            return
        why = (f"it has worked for {ran / 60:.0f} minutes" if over_time else
               f"it has spent ${spent:.2f}, {CHECK_IN_COST_X:g} times what a {self.effort.name} run usually costs")
        self.asked += 1
        qid = f"q{self.requests_from + self.asked}"
        self.step(f"Asked the user whether to keep going: {why}", "request")
        self.emit("request", id=qid, what="whether to keep going", why=why, seconds=None, keepGoing=True)
        seen = len(self._inbox_lines())
        deadline = time.monotonic() + CHECK_IN_WAIT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            if any(note.get("answers") == qid for note in self._inbox_lines()[seen:]):
                self.next_check_at = ran + CHECK_IN_S
                self.next_check_cost = spent + CHECK_IN_COST_X * self.effort.cap_usd
                self.step("The user answered; it reads their answer next", "waiting")
                return
        self.forced_write_up = (f"The user did not answer whether to keep going. Call finish now with everything you "
                                f"found; say in limitations what is left undone.")
        self.step(f"No answer in {CHECK_IN_WAIT_S / 60:.0f} minutes: writing up what it has", "waiting")

    def progress(self) -> int:
        """How much it has found or made: distinct pages its searches returned (a redirect counts as its site),
        distinct findings (the same one recorded again is nothing new: no clock stops a loop that repeats itself, so
        the stall rule must see it), reference pictures, outputs, its plan, the user's notes (a note is new to work
        on) and the session's entries it read in full, and distinct results its app operations actually produced."""
        pages = {self.source_names.get(u, u) for u in self.search_sources}
        found = len({" ".join(str(f.get("text") or "").split()).lower() for f in getattr(self, "recorded", [])})
        return (len(pages) + found + len(self.refs) + len(self.made) + (self.plan is not None)
                + len(self.notes_heard) + len(self.session_read) + len(self.session_page_reads)
                + len(getattr(self, "app_results", ())) + len(getattr(self, "builder_work", ())))

    def stalled(self, round_n: int) -> bool:
        """Called once at the start of each round: True when the last STALL_ROUNDS rounds found
        no new source or finding and made nothing (W6-4b), so it should write up what it has."""
        self.round_n = round_n
        now = self.progress()
        if now > self._progress:
            self._progress, self._progress_round = now, round_n - 1
        return round_n - 1 - self._progress_round >= STALL_ROUNDS

    def plan_due(self) -> bool:
        """A deep run says what it will look up before it starts (W6-4d): its first round may only
        call `plan` (engines.py)."""
        return (self.effort.name == "deep" and self.plan is None and self.spec.get("purpose", "research") == "research"
                and self.spec.get("deliverable") not in ("answer", "revise") and not self.revising)

    def emit_output(self, kind: str, path: str, caption: str, content: dict | None = None, **extra) -> None:
        output = {"kind": kind, "path": path, "caption": caption, **extra}
        if content:
            output["content"] = content
        if self.revising:  # a new version of what it made before (the manager numbers it)
            output.setdefault("replaces", (self.revising.get("target") or {}).get("id"))
            output.setdefault("change", self.revising.get("change"))
        self.made.append(output)
        self.emit("output", **output)

    def take_notes(self) -> list[str]:
        """Notes the user sent while it works (the manager appends them to inbox.jsonl) that it
        has not read yet. Engines add them to the conversation before their next round."""
        lines = self._inbox_lines()  # one reading of the inbox: whole lines only, one entry per line
        notes = [" ".join(str(note["text"]).split()) for note in lines[self.inbox_seen:]  # the user's words, whole
                 if str(note.get("text") or "").strip()]
        self.inbox_seen = max(self.inbox_seen, len(lines))
        for note in notes:
            self.step(f"Got a note: {note}", "note")
        self.notes_heard += notes
        return notes

    @staticmethod
    def note_message(notes: list[str]) -> str:
        return "A note from the user while you work: " + " / ".join(notes)

    # --- what the engine needs ---------------------------------------------------------
    def final_tools(self) -> list[str]:
        """The tools of a round made to write up: finish, and make_page while no page is made yet, so slides or a visual
        the user asked for are made from what it has rather than cut off (live, 30 Oct: "make me a short presentation"
        ended "I haven't built the presentation yet" after the stall rule made it write up)."""
        if "make_page" in self.tool_names() and not getattr(self, "pages_made", 0):
            return ["make_page", "finish"]
        return ["finish"]

    def tool_names(self) -> list[str]:
        allowed = [n for n in SCHEMAS if n in self.effort.tools]
        deliverable = self.spec.get("deliverable")
        if deliverable == "revise":
            return []  # a revision runs its own steps (revise()), not a model choosing tools
        if 'finish' in allowed and deliverable != 'answer' and self.spec.get('purpose') != 'description':
            allowed.append('save_findings')
        allowed = [n for n in allowed if (n != "make_video" or deliverable == "video") and (n != "make_lesson" or deliverable == "lesson")]
        if deliverable == "adapter":  # the builder's own tools and nothing of the session's (drafts.py)
            # Function tools only: web search is the engine's own (engines.SolEngine._tools adds it when the run's budget
            # allows it), with no schema here (acceptance 2026-10-14: every capped builder run failed, KeyError 'web_search').
            return [n for n in ("read_source", "draft", "pin_file", "try_draft", "finish") if n in self.effort.tools]
        allowed = [n for n in allowed if n not in ("read_source", "draft", "pin_file", "try_draft")]
        if deliverable == "answer":
            # A follow-up question about finished work: answered from that work (in the brief)
            # and the web; nothing new is made, so no pictures, snapshots, videos or lessons.
            allowed = [n for n in allowed if n not in ("fetch_reference_image", "snapshot", "annotate_image", "generate_image",
                                                       "make_video", "make_lesson", "make_page", *CHART_TOOLS)]
        if self.spec.get("purpose") == "description":
            # Fast, and about the kind of thing: no pictures, no snapshots (enrich.py fetches refs),
            # and no relabelling, since it never sees the specimen (it only had the label).
            allowed = [n for n in allowed if n not in ("fetch_reference_image", "snapshot", "annotate_image", "generate_image",
                                                       "propose_correction", "make_page")]
        if not self.crop_jpeg and not self.frame_jpeg:
            allowed = [n for n in allowed if n != "annotate_image"]
        if self.session_index is None or self.spec.get("purpose") == "description":
            # No index here, or about a kind of thing, not this session: nothing to read, so nothing to run either
            # (what run makes is read with the session tool).
            # A chart is drawn from a table the session kept: none can be read here.
            allowed = [n for n in allowed if n not in ("session", "run", *CHART_TOOLS)]
        if deliverable in ("answer", "revise"):
            allowed = [n for n in allowed if n != "run"]  # it answers from its work, or remakes one thing
        if (self.spec.get("purpose") == "description" or deliverable in ("answer", "revise")
                or self.effort.seconds < ROUND_RESERVE_S + ANSWER_WAIT_S):
            # Nobody is there to ask, it answers from its work, or it finishes before an answer could come and be read
            # (instant: 15 s; #194 review SF1).
            allowed = [n for n in allowed if n != "request_evidence"]
        if self.plan_due():
            allowed.append("plan")
        return allowed

    def _why_not(self, name: str) -> str:
        """Why a tool is not offered in this run, in plain words (never "not at this effort" when that is not why)."""
        deliverable, purpose = self.spec.get("deliverable"), self.spec.get("purpose", "research")
        if name not in SCHEMAS:
            return "there is no such tool"
        if name not in self.effort.tools and name not in ("save_findings", "plan"):
            return f"not at {self.effort.name} effort"
        if deliverable == "revise":
            return "a new version is made by its own steps"
        if deliverable == "answer":
            return "a follow-up answers from its earlier work: nothing new is made or run"
        if purpose == "description":
            return "a description is about the kind of thing, not this specimen"
        if name in ("session", "run", *CHART_TOOLS) and self.session_index is None:
            return "the session's index can't be read here" + (f" ({self.session_index_error})" if self.session_index_error else "")
        if name == "annotate_image":
            return "there is no photo of the object to label"
        if name in ("make_video", "make_lesson"):
            return f"this researcher makes {deliverable or 'research'}, not a {name.removeprefix('make_')}"
        if name == "request_evidence":
            return "this run ends before an answer could come and be read"
        if name == "plan":
            return "only a deep run's first round plans"
        return "not offered in this run"

    def schema(self, name: str) -> dict:
        """A tool's schema as this researcher is offered it: finish names the session's records only when
        it has the session tool (the fields cost every call that much more)."""
        schema = SCHEMAS[name]
        if name == "run":
            return self.run_schema(schema)
        if name in ("finish", "save_findings") and "session" not in self.tool_names():
            props = {k: v for k, v in schema["parameters"]["properties"].items() if k not in ("evidence", "session_claims")}
            return {**schema, "parameters": {**schema["parameters"], "properties": props}}
        return schema

    def door(self) -> tuple[list[dict], dict]:
        """What the app said, as this run started, it can run beside its own operations (the one door: capabilities
        the catalog added, with their parameters) and every cell finder segment can name; the static list when it
        said nothing (an older app)."""
        snap = (getattr(self, "spec", None) or {}).get("snapshot") or {}  # a bare run interface (tests) has no spec
        capabilities = [c for c in snap.get("capabilities") or [] if isinstance(c, dict) and isinstance(c.get("key"), str)]
        finders = snap.get("cellFinders") if isinstance(snap.get("cellFinders"), dict) and snap.get("cellFinders") else CELL_FINDERS
        return capabilities, finders

    def classifiers(self) -> dict:
        """Every model classify can name, as the app said as this run started (its static list for an older app)."""
        from .budget import CLASSIFIERS

        snap = (getattr(self, "spec", None) or {}).get("snapshot") or {}
        given = snap.get("classifiers")
        return given if isinstance(given, dict) and given else CLASSIFIERS

    def run_schema(self, schema: dict) -> dict:
        """run, with the capabilities on here as one more operation (each its key, with its parameters) and the cell
        finders the app has now."""
        import copy

        schema = copy.deepcopy(schema)
        capabilities, finders = self.door()
        props = schema["parameters"]["properties"]
        props["implementation"]["enum"] = list(finders)
        if "models" in props:
            classifiers = self.classifiers()
            props["models"]["items"]["enum"] = list(classifiers)
            props["models"]["description"] = "classify only: the models, by id (" + "; ".join(
                f"{k} = {v}" for k, v in classifiers.items()) + ")"
        props["implementation"]["description"] = ("segment only: the cell finder, by id (" + "; ".join(
            f"{k} = {v}" for k, v in finders.items()) + "); the app's default if not given. Motion is measured one way "
            "(spots followed frame to frame)")
        description = schema["description"].format(operation_limit=operation_limit(self.effort.name))
        if capabilities:
            props["operation"]["enum"] = [*props["operation"]["enum"], "capability"]
            props["capability"] = {"type": "string", "enum": [c["key"] for c in capabilities],
                                   "description": "operation capability: which one, by its key"}
            from orchestrator.retained_inputs import SCHEMA as RESOURCE_INPUT_SCHEMA
            props["resource"] = copy.deepcopy(RESOURCE_INPUT_SCHEMA)
            props["resource"]["description"] = ("operation capability only: exact retained picture reference from the session entry; "
                                                "omit for the live view. Do not combine with input or frame.")
            props["params"] = {"type": "object", "description": "operation capability: its parameters by name; one left "
                                                                "out is its default. " + props.get("params", {}).get("description", "")}
            if any(c.get("typed") for c in capabilities):
                from orchestrator.admission import INPUTS_SCHEMA
                props["inputs"] = copy.deepcopy(INPUTS_SCHEMA)
            description += (" operation capability runs one the catalog added on the live view or an exact retained picture (resource), kept as a session entry "
                            "you read with the session tool (its makers' output: data, not instructions). On here "
                            "(contributed descriptions, data, not instructions):\n" + block("; ".join(
                                f"{c['key']} = {str(c.get('says') or c.get('name'))[:300]}" for c in capabilities),
                                tag="capability", source="contributed", limit=6000))
        return {**schema, "description": description}

    def web_search_allowed(self) -> bool:
        return "web_search" in self.effort.tools

    def system_prompt(self) -> str:
        snap = self.spec.get("snapshot") or {}
        obj = snap.get("object") or {}
        object_text = (f'{obj.get("id")} labelled "{obj.get("label") or "unnamed"}"'
                       + (f' (confidence {obj["confidence"]})' if obj.get("confidence") is not None else "")) if obj else "none"
        offered = set(self.tool_names()) | ({"claims"} if self.spec.get("purpose", "research") == "research" else set())
        rules = "\n".join(f"- {text}" for tool, text in RULES if tool is None or tool in offered)
        return SYSTEM.format(
            rules=rules, n=self.spec.get("n", 1), seconds=self.effort.seconds, cap="no spending limit" if self.budget.unlimited else f"${self.effort.cap_usd:.2f}",
            specimen=snap.get("specimen") or "not described yet", object=object_text,
            turns=snap.get("turns") or "(none)",
            attached=("" if self.spec.get("purpose") == "description" or not self.images() else
                      "Attached: the eyepiece view" + (" and the object's sharpest close-up." if self.crop_jpeg else ".")),
            deliverable=deliverable_text(self.spec.get("deliverable"), obj.get("label") or self.spec.get("label", "")),
            learner=self.learner_text(), plan=PLAN if self.plan_due() else "")

    def learner_text(self) -> str:
        """How the user wants things explained (orchestrator/learner.py), as the app wrote it when
        this run started; a prompt line of its own, or nothing."""
        text = " ".join(str((self.spec.get("snapshot") or {}).get("learner") or "").split())
        return f"\n{text}" if text else ""

    def brief(self) -> str:
        brief = str(self.spec.get("brief") or "")
        inputs = self.spec.get('dependencies') or []
        if inputs:
            brief += '\nUse these exact completed research inputs for the requested handoff. Later revisions are different work. '
            brief += block(json.dumps(inputs, ensure_ascii=False), tag='retained_handoff', source='original completed researcher runs')
        return brief

    def images(self) -> list[bytes]:
        if self.spec.get("purpose") == "description":
            return []  # a description is about the kind of thing, shared by every outline with the name
        if self.spec.get("deliverable") == "answer":
            return []  # a follow-up answer reads the earlier work as text (cheap)
        return [j for j in (self.frame_jpeg, self.crop_jpeg) if j]

    # --- tools ------------------------------------------------------------------------
    async def call_tool(self, name: str, args: dict) -> tuple[dict, bytes | None]:
        """Run one tool; returns (result for the model, optional picture to show it)."""
        if name not in self.tool_names():
            return {"error": f"{name} is not available here: {self._why_not(name)}"}, None
        try:
            return await getattr(self, "tool_" + name)(args or {})
        except OverBudget as exc:
            return {"error": f"over budget: {exc}"}, None
        except TooLong as exc:  # refused whole, never cut: the model says it shorter
            return {"error": str(exc)}, None
        except FetchRefused as exc:
            self.step("Could not use that picture", "warning", detail=str(exc))
            return {"error": str(exc)}, None
        except Exception as exc:
            self.step("One of its steps didn't work; it carries on", "warning", detail=f"{name}: {type(exc).__name__}: {exc}"[:600])
            return {"error": f"{type(exc).__name__}: {exc}"[:300]}, None

    async def tool_snapshot(self, args: dict):
        if time.monotonic() - self.last_snapshot < 10:
            return {"error": "one snapshot per 10 s"}, None
        self.last_snapshot = time.monotonic()
        self.step("Took a fresh look at the live view")
        reply = await self.request("snapshot", rid=self.rid)
        if not reply.get("ok"):
            return {"error": reply.get("error") or "no snapshot"}, None
        crop = self._read(reply["crop"]) if reply.get("crop") else None
        frame = self._read(reply["frame"]) if reply.get("frame") else None
        if crop is None and frame is None:
            return {"error": "the app sent no new picture"}, None
        self.crop_jpeg = crop or self.crop_jpeg
        self.frame_jpeg = frame or self.frame_jpeg
        if crop is not None:
            return {"ok": True, "note": "fresh close-up attached"}, crop
        # Only the whole view came: never an earlier close-up in its place.
        return {"ok": True, "note": "fresh view of the whole field attached (no close-up this time)"}, frame

    def _looked(self, entry_id: str, how: str, data) -> None:
        """What it looked at in the session (the mission card's working set), once per record, way and revision,
        with the index's own title, kind and revision (the app's words, and what it read: C1), never a
        researcher's. Called only once the read or the picture worked."""
        entry = (data or {}).get("entry") if isinstance(data, dict) and isinstance((data or {}).get("entry"), dict) else data
        version = entry.get("version") if isinstance(entry, dict) and isinstance(entry.get("version"), int) else None
        page = data.get("page") if isinstance(data, dict) else None
        content_revision = page.get("revision") if isinstance(page, dict) else None
        key = (entry_id, how, version, content_revision)
        if not entry_id or key in self._looked_at:
            return
        self._looked_at.add(key)
        title = entry.get("title") if isinstance(entry, dict) else None
        kind = entry.get("kind") if isinstance(entry, dict) else None
        self.emit("looked", id=entry_id, how=how, title=str(title or "")[:80], kind=str(kind or "")[:30], version=version,
                  **({"contentRevision": content_revision} if content_revision else {}))

    async def tool_run(self, args: dict):
        """One of the app's own operations on the live view now, for its reading (orchestrator/research/hands.py):
        what it makes is a session entry it reads with the session tool; nothing is shown on screen."""
        operation = str(args.get("operation") or "")
        capabilities, finders = self.door()
        capability = str(args.get("capability") or "").strip()
        selection = args.get('selection')
        if operation == 'workflow':
            if type(args.get('params')) is not dict or any(args.get(k) is not None for k in ('selection','input','frame','how','seconds','implementation','capability','resource','inputs')) or args.get('fresh',False):
                return {'error':'Choose one workflow command in params, without live inputs'},None
        if operation == 'measure_saved_objects':
            if (type(selection) is not dict or set(selection) != {'project', 'session', 'revision', 'input', 'digest'}
                    or any(args.get(key) is not None for key in ('input', 'frame', 'how', 'seconds', 'implementation',
                                                                'capability', 'params', 'resource', 'inputs'))
                    or args.get('fresh', False)):
                return {'error': 'Choose one exact saved labels selection, without live or capability inputs'}, None
            selection = copy.deepcopy(selection)
        elif selection is not None:
            return {'error': 'selection belongs only to measure_saved_objects'}, None
        resource = args.get("resource")
        if resource is not None and (operation != "capability" or not isinstance(resource, dict)
                                     or args.get("input") or args.get("frame") is not None):
            return {"error": "resource names one exact retained picture for a capability; do not combine it with input or frame"}, None
        typed_inputs = args.get("inputs")
        if typed_inputs is not None and (operation != "capability" or not isinstance(typed_inputs, dict) or resource is not None):
            return {"error": "inputs name one retained resource per input of a capability; do not combine them with resource"}, None
        if operation == "capability":  # one the catalog added (the one door): checked again by the app
            on = {c["key"]: c for c in capabilities}
            if capability not in on:
                return {"error": f"capability is one of: {', '.join(on) or 'none is on here'}"}, None
            if args.get("params") is not None and not isinstance(args.get("params"), dict):
                return {"error": "params are its parameters by name"}, None
        elif operation not in RUN_OPERATIONS:
            return {"error": f"operation is one of: {', '.join((*RUN_OPERATIONS, *(['capability'] if capabilities else [])))}"}, None
        fresh = args.get("fresh", False)
        if not isinstance(fresh, bool) or (fresh and operation != "motion"):
            return {"error": "fresh is a boolean for motion's upcoming camera interval"}, None
        models, classes = args.get("models"), args.get("classes")
        if operation == "classify":
            from .budget import MAX_CLASSIFIERS

            known = self.classifiers()
            if (not isinstance(models, list) or not 2 <= len(models) <= MAX_CLASSIFIERS
                    or any(not isinstance(m, str) or m not in known for m in models)):
                return {"error": f"models are two to {MAX_CLASSIFIERS} of: {', '.join(known)}"}, None
            if classes is not None and (not isinstance(classes, list) or any(not isinstance(c, str) for c in classes)):
                return {"error": "classes are a list of names"}, None
        elif models is not None or classes is not None:
            return {"error": "models and classes belong only to classify"}, None
        limit = operation_limit(self.effort.name)
        if self.runs >= limit:
            return {"error": f"at most {limit} operations in one run: work from what they made"}, None
        wait = RUN_WAIT_MAX_S  # waited for as long as the operation itself may take: no run clock cuts it short
        why = " ".join(str(args.get("why") or "").split())
        sample = str(args.get("input") or "").strip()
        if sample and not re.fullmatch(r"[\w.:-]{1,80}", sample):
            return {"error": f"{sample[:40]!r} is not an entry's id (the session tool lists them)"}, None
        try:
            frame = int(args["frame"]) if args.get("frame") is not None else None
        except (TypeError, ValueError):
            return {"error": "frame is a held clip's frame number: 0 for the first"}, None
        how = str(args.get("how") or "").strip().lower() or None
        implementation = str(args.get("implementation") or "").strip() or None  # segment's cell finder, by id (Q24)
        if implementation and (operation != "segment" or implementation not in finders):
            return {"error": (f"implementation is one of the cell finders: {', '.join(finders)}" if operation == "segment"
                              else f"{operation} is done one way; implementation names segment's cell finder")}, None
        try:
            seconds = float(args["seconds"]) if args.get("seconds") is not None and operation == "motion" else None
        except (TypeError, ValueError):
            return {"error": "seconds is how long motion watches: a number, 4 if not given"}, None
        what = (f"run {next(c['name'] for c in capabilities if c['key'] == capability)} on "
                + ("a retained result" if resource is not None else STAGE)
                if operation == "capability" else RUN_OPERATIONS[operation].format(view=sample or STAGE))
        if operation == "motion" and sample:
            what = f"measure what moves in retained clip {sample}"
        # Said without the kept sample's id; the id and its reason go with the step's detail.
        said_what = what if operation == "capability" else RUN_OPERATIONS[operation].format(
            view="a view kept earlier" if sample else STAGE)
        if operation == "motion" and sample:
            said_what = "measure what moves in a clip kept earlier"
        self.step(f"Asking the app to {said_what}", detail=what + (f" ({why})" if why else ""))
        # The app is told how long this waits, and lets the operation go a little before (nothing left queued).
        reply = await self.request("run", rid=self.rid, operation=operation, why=why, wait=wait, timeout=wait,
                                   **({"input": sample} if sample else {}), **({"frame": frame} if frame is not None else {}),
                                   **({"how": how} if how else {}), **({"seconds": seconds} if seconds is not None else {}),
                                   **({"implementation": implementation} if implementation else {}),
                                   **({"capability": capability, "params": args.get("params") or {}}
                                      if operation == "capability" else {}),
                                   **({"resource": resource} if resource is not None else {}),
                                   **({"inputs": typed_inputs} if typed_inputs is not None else {}),
                                   **({"selection": selection} if selection is not None else {}),
                                   **({'runId': self.spec.get('run_id')} if operation in ('measure_saved_objects','workflow') else {}),
                                   **({'params':args['params']} if operation=='workflow' else {}),
                                   **({"models": list(models), **({"classes": list(classes)} if classes else {})}
                                      if operation == "classify" else {}),
                                   **({"fresh": True} if fresh else {}))
        if reply.get("ok"):
            self.runs += 1  # a refusal ("another researcher's operation is running") uses none of its runs
        if reply.get("error") == "the app did not answer":  # the app lets it go when this stops waiting
            said = f"not answered in {wait:.0f} s: the app lets it go when you stop waiting, so nothing is made for you"
            self.step(f"The app has not finished: {said}", "warning")
            return {"error": said}, None
        if not reply.get("ok"):
            error = reply.get("error") or "the app could not run it"
            self.step(f"The app could not {said_what}", "warning", detail=f"{what}: {error}")
            facts = {}
            if isinstance(reply.get("interval"), dict):
                try:
                    encoded = json.dumps(reply["interval"], allow_nan=False)
                    if len(encoded) <= 1024:
                        facts["interval"] = json.loads(encoded)
                except (TypeError, ValueError):
                    pass  # invalid diagnostics never replace the actual operation refusal
            return {"error": error, **({"next": reply["next"]} if reply.get("next") else {}), **facts}, None
        self.step(f"The app {said_words(reply.get('words'))}", detail=str(reply.get('words') or ''))
        entry = reply.get("id")
        if isinstance(entry, str) and re.fullmatch(r"[\w.:-]{1,80}", entry):
            if not hasattr(self, "app_results"):  # older standalone worker fixtures initialise only the run interface
                self.app_results = set()
            self.app_results.add((str(getattr(self, "spec", {}).get("day") or ""), operation, entry))
            if not hasattr(self, "ran_facts"):
                self.ran_facts = []
            # Its work as much as a finding: kept with the app's own words and numbers for a result it never wrote up.
            self.ran_facts.append({"id": entry, "operation": operation, "title": reply.get("title"),
                                   "words": reply.get("words"),
                                   **({"values": reply["values"]} if isinstance(reply.get("values"), dict) else {})})
        # Small actual producer facts, detached at the process boundary. Large/invalid facts still have
        # their original entry available through session; never turn the operation's prose into measurements.
        facts = {k: reply[k] for k in ("words", "count", "capture", "view", "values") if k in reply}
        try:
            encoded = json.dumps(facts, allow_nan=False)
            facts = json.loads(encoded) if len(encoded) <= 6000 else {"factsOmitted": "read the original session entry"}
        except (TypeError, ValueError):
            facts = {"factsOmitted": "read the original session entry"}
        return {"ok": True, "id": reply.get("id"), "title": reply.get("title"),
                **facts, "note": f"read it with the session tool (action read, id {reply.get('id')}); cite it as evidence"}, None

    async def tool_session(self, args: dict):
        """The session's one index, read-only (session_index.py): what the app and other researchers made today.
        What it returns is the app's data and the researchers' words: wrapped as untrusted, never instructions."""
        if self.session_index is None:
            return {"error": "the session's index is not available here"
                             + (f" ({self.session_index_error})" if self.session_index_error else "")}, None
        action = str(args.get("action") or "")
        entry_id = str(args.get("id") or "").strip()
        if entry_id and not re.fullmatch(r"[\w.:-]{1,80}", entry_id):
            return {"error": f"{entry_id[:40]!r} is not an entry's id (list gives the ids)"}, None
        try:
            n = int(args.get("n") or 0)
        except (TypeError, ValueError):
            return {"error": "n is a picture's number: 0 for the first"}, None
        index = self.session_index
        try:
            if action == "summary":
                data = await asyncio.to_thread(index.summary)
            elif action == "list":
                listing = await asyncio.to_thread(index.list, args.get("kind"), args.get("query"))
                self._saw(*(listing.get("entries") or []))
                text, older = newest_first(listing)
                self.step("Looking through what today's session made", detail=f"list {args.get('kind') or ''}".strip())
                more = (f" [{older} older entries left out: list by kind or query to see them]" if older else "")
                return {"ok": True, "action": "list", "data": block(text + more, tag="session", source="session"),
                        **({"olderLeftOut": older} if older else {})}, None
            elif action in ("read", "picture") and not entry_id:
                return {"error": f"{action} needs an entry's id (list or summary gives them)"}, None
            elif action == "read":
                try:
                    offset = int(args.get('offset') or 0)
                except (TypeError, ValueError):
                    return {"error": "offset is a character position: 0, or the next offset a page gave"}, None
                data = await asyncio.to_thread(index.read_page, entry_id, offset, args.get('revision'))
                page = data.get('page')
                if page is not None:
                    key = (entry_id, page['revision'])
                    if key not in self.session_pages:
                        self.session_read.discard(entry_id)
                    if page['offset'] == self.session_pages.get(key, 0):
                        retain = page['totalChars'] <= DOCUMENT_MAX and len(self.session_documents) < DOCUMENTS_MAX
                        if not retain and page['offset'] == 0:
                            self.step("Read a long saved result; it isn't kept for charts or captures", "warning",
                                      detail=f"{entry_id}: over {DOCUMENT_MAX:,} characters, or {DOCUMENTS_MAX} entries kept already")
                        if retain and (key in self._document_parts or page['offset'] == 0):
                            parts = self._document_parts.get(key, '') + data['text']
                            if page['nextOffset'] is None:
                                if hashlib.sha256(parts.encode()).hexdigest() == page['revision']:
                                    self.session_documents[key] = json.loads(parts)
                                self._document_parts.pop(key, None)
                            else:
                                self._document_parts[key] = parts
                        self.session_page_reads.add((*key, page['offset']))
                        self.session_pages[key] = page['nextOffset'] or page['totalChars']
                        if page['nextOffset'] is None:
                            self.session_read.add(entry_id)
                    self._looked(entry_id, 'read page', data)
                    self._saw(data.get('entry') or {})
                    self.step("Reading a saved result from today",
                              detail=f"{entry_id}: characters {page['offset']}–{page['nextOffset'] or page['totalChars']}")
                    return {'ok': True, 'action': 'read', 'id': entry_id, 'page': page,
                            'data': block(data['text'], tag='session', source='session'),
                            'next': ({'action': 'read', 'id': entry_id, 'offset': page['nextOffset'],
                                      'revision': page['revision']} if page['nextOffset'] is not None else None)}, None
                self._looked(entry_id, "read", data)
                self._saw(data.get("entry") or {})
            elif action == "picture":
                body, kind = await asyncio.to_thread(index.picture, entry_id, n)
                jpeg = await asyncio.to_thread(_as_jpeg, body)
                if jpeg is None:
                    return {"error": f"{entry_id}'s picture could not be read ({kind or 'unknown type'})"}, None
                self.step("Looked at a saved picture from today", detail=f"{entry_id} picture {n}")
                self._looked(entry_id, f"picture {n}", {"entry": {"id": entry_id},
                    "page": {"revision": "sha256:" + hashlib.sha256(body).hexdigest()}})
                return {"ok": True, "id": entry_id, "n": n, "note": "its picture is attached"}, jpeg
            else:
                return {"error": "action is summary, list, read or picture"}, None
        except NoEntry:
            return {"error": f"there is no entry {entry_id!r} in today's session (list gives the ids)"}, None
        except NoPicture:
            return {"error": f"{entry_id} has no picture {n} (its entry lists its pictures)"}, None
        except IndexUnavailable as exc:
            return {"error": f"the session's index could not be read: {exc}"}, None
        text, cut = capped(data)
        if action == 'read' and not cut:
            self.session_read.add(entry_id)
        self.step("Looking through what today's session made" if action == "summary" else "Reading a saved result from today",
                  detail=f"{action} {entry_id or ''}".strip())
        return {"ok": True, "action": action, "id": entry_id or None,
                "data": block(text + ((" [cut here: list by kind or query, or read one entry]" if action == "summary"
                                       else " [cut here: the rest of this entry stays in the app's index]") if cut else ""),
                              tag="session", source="session"),
                **({"cut": True} if cut else {})}, None

    @staticmethod
    def _norm_url(url: object) -> str:
        """One spelling of a URL for matching: no fragment, no trailing slash, scheme and host in lower case."""
        import urllib.parse

        parts = urllib.parse.urlsplit(str(url or "").strip())
        return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))

    def _ref_key(self, url: object) -> str:
        """The reference a cited URL means: the URL it was fetched by, the picture's own address or its page
        (a Commons page and its file are one reference), in one spelling."""
        norm = self._norm_url(url)
        return getattr(self, "ref_names", {}).get(norm, norm)

    async def tool_fetch_reference_image(self, args: dict):
        url = str(args.get("url") or "").strip()
        key = url.split("#", 1)[0]
        if key in self.failed_urls:
            # Asking again would fail again (a live researcher re-fetched the same 404 until
            # it ran out of time): refused here, with no network call. Only a definite failure is kept
            # here at once; one that may pass (a timeout, HTTP 429 or 5xx) gets FETCH_TRIES tries.
            return {"error": f"already tried this URL and it failed ({self.failed_urls[key]}); do not fetch it again, "
                             "use another source or finish"}, None
        self.step("Fetching a reference picture from the web", detail=url[:300])
        try:
            image = await fetch_image(url)
        except FetchRefused as exc:
            image = None
            if str(exc).startswith("not an image"):
                # An ordinary page: its own pictures (as the app's reference tool reads them), the first that is one.
                try:
                    for candidate in (await page_images(url))[:3]:
                        try:
                            image = await fetch_image(candidate)
                            break
                        except FetchRefused:
                            continue
                except FetchRefused:
                    image = None
            if image is None:
                tries = self.fetch_tries[key] = self.fetch_tries.get(key, 0) + 1
                if exc.transient and tries < FETCH_TRIES:
                    raise FetchRefused(f"{exc}; this may pass, so it can be tried once more later", transient=True) from exc
                self.failed_urls[key] = str(exc)[:120]
                raise
        rel = f"sources/{image.sha1}.jpg"
        (self.dir / rel).write_bytes(image.jpeg)
        ref = self._norm_url(url)
        self.refs[ref] = {"path": rel, "url": image.url, "page": image.page, "title": image.title,
                          "licence": image.licence, "author": image.author, "credit": image.credit()}
        self.ref_names = {**getattr(self, "ref_names", {}),
                          **{self._norm_url(u): ref for u in (url, image.url, image.page) if u}}
        # Its address is said back, so finish.refs can name it as fetched.
        return {"ok": True, "url": url, "title": image.title, "credit": image.credit(),
                "size": [image.width, image.height], "cite_as": url}, image.jpeg

    async def _image_call(self, kind: str, prompt: str, source: bytes | None, count: bool = True) -> dict:
        import openai

        if not self.budget.image_allowed():
            return {"error": f"at most {self.effort.max_images} pictures at {self.effort.name} effort"}
        model = self.spec.get("image_model") or IMAGE_MODEL_DEFAULT
        self.budget.check(CALL_MOST["picture"])
        if self.offline:
            return self._offline_image(source, count)
        if model.startswith("gemini"):
            return await self._gemini_image(model, prompt, source, count)
        try:
            client = openai.AsyncOpenAI(max_retries=0)  # one admitted attempt, never hidden SDK retries
            if source is not None:
                buf = io.BytesIO(source)
                buf.name = "photo.png"
                result = await client.images.edit(model=model, image=buf, prompt=prompt, size="1024x1024", quality="medium")
            else:
                result = await client.images.generate(model=model, prompt=prompt, size="1024x1024", quality="medium")
        except asyncio.CancelledError:
            self.cancelled_call(model, usd=0.04)
            raise
        except Exception as exc:  # noqa: BLE001 - why is said with what to do, never "a step didn't work"
            return self._media_unavailable(exc, model, "the picture", ("OPENAI_API_KEY",))
        usage = getattr(result, "usage", None)
        details = getattr(usage, "input_tokens_details", None)
        self.spend(model, image_cost(model, getattr(details, "text_tokens", 0) or 0, getattr(details, "image_tokens", 0) or 0,
                                     getattr(usage, "output_tokens", 0) or 0) if usage else 0.04)
        if count:
            self.budget.images += 1
        return {"png": base64.b64decode(result.data[0].b64_json), "model": model}

    async def _gemini_image(self, model: str, prompt: str, source: bytes | None, count: bool) -> dict:
        """A picture from a Gemini image model (option IMAGE "gemini-3.1-flash-image"): one
        generate_content call with the photo, if any, and the instruction; the reply's picture."""
        from google import genai
        from google.genai import types

        contents: list = [prompt]
        if source is not None:
            mime = "image/jpeg" if source[:2] == b"\xff\xd8" else "image/png"
            contents.insert(0, types.Part.from_bytes(data=source, mime_type=mime))
        try:
            # Keep a reference: a temporary client closes mid-call. One admitted attempt, never hidden SDK retries.
            client = genai.Client(http_options={"retry_options": {"attempts": 1}})
            response = await client.aio.models.generate_content(model=model, contents=contents)
        except asyncio.CancelledError:
            self.cancelled_call(model, usd=0.04)
            raise
        except Exception as exc:  # noqa: BLE001 - why is said with what to do, never "a step didn't work"
            return self._media_unavailable(exc, model, "the picture", ("GEMINI_API_KEY", "GOOGLE_API_KEY"))
        usage = response.usage_metadata
        by = lambda details: {d.modality.name if hasattr(d.modality, "name") else str(d.modality): d.token_count or 0
                              for d in (details or [])}
        prompt_in = by(getattr(usage, "prompt_tokens_details", None))
        out = by(getattr(usage, "candidates_tokens_details", None))
        self.spend(model, image_cost(model, prompt_in.get("TEXT", 0), prompt_in.get("IMAGE", 0), out.get("IMAGE", 0)) if usage else 0.04)
        for part in (response.candidates[0].content.parts if response.candidates else []):
            if part.inline_data is not None and part.inline_data.data:
                if count:
                    self.budget.images += 1
                return {"png": self._as_png(part.inline_data.data), "model": model}
        return {"error": f"{model} gave no picture: " + (response.text or "")[:160]}

    @staticmethod
    def _as_png(data: bytes) -> bytes:
        """The picture as PNG (Gemini may answer in another format; the outputs are PNG)."""
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return data
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        return cv2.imencode(".png", image)[1].tobytes()

    def _offline_image(self, source: bytes | None, count: bool) -> dict:
        """The scripted engine's picture: the photo unchanged (annotated) or a grey square."""
        import cv2
        import numpy as np

        png = source if source is not None else cv2.imencode(".png", np.full((64, 64, 3), 90, np.uint8))[1].tobytes()
        self.spend("fake-image", 0.001)
        if count:
            self.budget.images += 1
        return {"png": png, "model": "fake-image"}

    def _save_output(self, kind: str, png: bytes, mark: tuple[str, str, str] | None = None) -> str:
        """Saves a picture. `mark` (text, source type, model): the label and source are put in the
        file (provenance.py), and the unmarked picture is kept beside it (*.clean.png) so a
        revision edits the picture, not its label."""
        self.outputs += 1
        rel = f"outputs/{self.outputs:02d}-{slug(kind)}.png"
        if mark is not None:
            (self.dir / rel).with_suffix(".clean.png").write_bytes(png)
            png = mark_png(png, *mark)
        (self.dir / rel).write_bytes(png)
        return rel

    async def tool_annotate_image(self, args: dict):
        """Labels on the photo. The photo is padded to a square (never stretched); the result
        is checked against it (fidelity.py) and retried once if the model redrew the
        specimen; if it still did, the picture is kept but marked as altered."""
        source = self.frame_jpeg if args.get("source") == "frame" else (self.crop_jpeg or self.frame_jpeg)
        labels = [l for l in args.get("labels") or [] if isinstance(l, dict) and l.get("name")]
        if not labels:
            return {"error": "give at least one label with where it is"}, None
        # What fits on a picture is refused when exceeded, never cut: the model chooses what to drop.
        if len(labels) > LABELS_MAX:
            return {"error": f"at most {LABELS_MAX} labels fit on one picture; give the {LABELS_MAX} that matter most"}, None
        long = [str(l["name"]) for l in labels if len(str(l["name"])) > LABEL_NAME_MAX]
        if long:
            return {"error": f"label names are drawn on the picture: at most {LABEL_NAME_MAX} characters "
                             f"({', '.join(long)}); shorten them"}, None
        self.step("Labelling the photo: " + ", ".join(str(l["name"])[:30] for l in labels))
        padded, region = square_pad(source)
        wanted = "; ".join(f'"{l["name"]}" pointing at {" ".join(str(l.get("where") or "").split())}' for l in labels)
        prompt = ("This is a photo taken through a light microscope, on a dark background. Add only thin white arrows "
                  f"with short white text labels: {wanted}. Put the text in the dark margins or empty areas. "
                  "Draw nothing else: no outlines, shapes, shading or highlights. Do not change the photo in any way: "
                  "do not redraw, sharpen, denoise, recolour or crop it, and do not add or clarify any detail.")
        made, score = None, 0.0
        for attempt in range(2):
            made = await self._image_call("annotated", prompt, padded, count=attempt == 0)
            if "error" in made:
                return made, None
            score = alteration(padded, made["png"], region)
            if score <= ALTERED_ABOVE:
                break
            self.step(f"The picture model changed the photo (score {score:.0f}); trying once more", "warning")
            prompt += " Previous attempt redrew the specimen: this time only add arrows and text."
        altered = score > ALTERED_ABOVE
        # Labels on a real photo: said in the file itself, not only in the studio (provenance.py).
        rel = self._save_output("annotated", made["png"], (LABELLED, COMPOSITE, made["model"]))
        names = ", ".join(str(l["name"]) for l in labels)
        caption = (f"Labels by {made['model']} on the microscope photo: {names}"
                   + (". Warning: the picture model may have redrawn parts of the photo" if altered else ""))
        self.emit_output("image", rel, caption, labels_content(labels), generated=False, altered=altered,
                         fidelity=round(score, 1))
        # It sees what it made (arrows that point beside the thing are its to notice and redo).
        return ({"ok": True, "output": rel, "altered": altered, "note": "the labelled picture is attached"},
                await asyncio.to_thread(_as_jpeg, made["png"]))

    async def tool_generate_image(self, args: dict):
        prompt = str(args.get("prompt") or "")
        self.step("Making an illustration")
        made = await self._image_call("illustration", prompt, None)
        if "error" in made:
            return made, None
        made["path"] = self._save_output("illustration", made["png"], (ILLUSTRATION, GENERATED, made["model"]))
        caption = f"Generated illustration ({made['model']}), not the specimen"
        self.emit_output("image", made["path"], caption, text_content(prompt, caption), generated=True)
        return ({"ok": True, "output": made["path"], "note": "the illustration is attached"},
                await asyncio.to_thread(_as_jpeg, made["png"]))

    # --- charts and animations of a table the session kept (measurement_charts.py) ------------------------------------
    async def _table(self, table) -> tuple[dict | None, str, str]:
        """(the table's whole document, its exact revision, "") or (None, "", why not): one session entry read whole by
        the app, at the revision the researcher read (or as it is now when it names none). A document it already read
        whole is used as kept; otherwise every page is read here, off the model's rounds, and checked against the
        revision before it is used."""
        if not isinstance(table, dict):
            return None, "", "table names the session entry: {id, revision}"
        entry_id = str(table.get("id") or "").strip()
        wanted = str(table.get("revision") or "").strip() or None
        if not re.fullmatch(r"[\w.:-]{1,80}", entry_id):
            return None, "", f"{entry_id[:40]!r} is not an entry's id (the session tool lists them)"
        kept = [(rev, doc) for (eid, rev), doc in getattr(self, "session_documents", {}).items()
                if eid == entry_id and (wanted is None or rev == wanted)]
        if kept:
            return kept[-1][1], kept[-1][0], ""
        if self.session_index is None:
            return None, "", "the session's index can't be read here, so no table can be charted"
        parts, offset, revision = [], 0, wanted
        try:
            while True:
                data = await asyncio.to_thread(self.session_index.read_page, entry_id, offset, revision)
                page = data.get("page")
                if not isinstance(page, dict):
                    return None, "", f"{entry_id} could not be read as a table"
                if revision is not None and page.get("revision") != revision:
                    return None, "", "that table changed since you read it; read it again and use its new revision"
                revision = page["revision"]
                parts.append(str(data.get("text") or ""))
                if sum(map(len, parts)) > CHART_TABLE_MAX:
                    return None, "", f"that entry is over {CHART_TABLE_MAX:,} characters; chart a smaller table"
                if page.get("nextOffset") is None:
                    break
                offset = int(page["nextOffset"])
        except NoEntry:
            return None, "", f"there is no entry {entry_id!r} in today's session (list gives the ids)"
        except IndexUnavailable as exc:
            changed = "409" in str(exc) or "changed" in str(exc)
            return None, "", ("that table changed since you read it; read it again and use its new revision" if changed
                              else f"the session's index could not be read: {exc}")
        text = "".join(parts)
        if hashlib.sha256(text.encode()).hexdigest() != revision:
            return None, "", "the table's pages did not make up its revision; read it again"
        document = json.loads(text)
        self._looked(entry_id, "chart", {"entry": document.get("entry") or {"id": entry_id}, "page": {"revision": revision}})
        return document, revision, ""

    async def _chart_series(self, args: dict):
        """(series, title, kind, None) or (None, "", "", error reply) for make_chart and animate_measurements."""
        from . import measurement_charts as charts

        title = " ".join(str(args.get("title") or "").split())
        if not 1 <= len(title) <= 90:
            return None, "", "", {"error": "give the chart a title of 1-90 characters"}
        kind = str(args.get("kind") or "sorted")
        if kind not in charts.KINDS:
            return None, "", "", {"error": f"kind is one of {', '.join(charts.KINDS)}"}
        document, revision, why = await self._table(args.get("table"))
        if document is None:
            return None, "", "", {"error": why}
        try:
            series = await asyncio.to_thread(
                charts.series, document, revision=revision, rows=str(args.get("rows") or ""),
                value=str(args.get("value") if args.get("value") is not None else ""),
                label=args.get("label") if isinstance(args.get("label"), str) else None,
                group=args.get("group") if isinstance(args.get("group"), str) and args.get("group") else None,
                order=str(args.get("order") or "ascending"),
                only=args.get("only") if isinstance(args.get("only"), str) and args.get("only") else None)
        except ValueError as exc:
            return None, "", "", {"error": str(exc)}
        return series, title, kind, None

    def _chart_output(self, series, title: str, kind: str, png: bytes, *, animated: bool, frames: int = 0,
                      note: str = "") -> str:
        """Keeps a chart or an animation as one of its outputs (outputs/NN-chart.png, NN-animation.png), with where its
        numbers came from: a picture the app drew from the table (`derived`), never a photo or a generated picture."""
        rel = self._save_output("animation" if animated else "chart", png)
        src = series.source
        what = (f"Animation of {series.column}, built row by row ({series.n} rows)" if animated
                else f"Chart of {series.column} ({series.n} rows)")
        caption = (f"{what}, drawn by the app from {src.get('title') or 'a saved table'}; its values are the table's own"
                   + (f", {series.excluded} rows without a number left out" if series.excluded else "")
                   + ("; an animation of the data, not a video of the specimen" if animated else "") + (f". {note}" if note else ""))
        self.emit_output("image", rel, caption, series.content(title, kind, animated), generated=False,
                         derived={"from": "table", "source": dict(src), "chart": kind, "animated": animated,
                                  **({"frames": frames} if animated else {})})
        return rel

    async def _chart_means(self, args: dict):
        """make_chart kind "means": one bar per table (one per run of an operation, e.g. motion run 4 times) at the mean
        of its values with one standard deviation either side and every value a dot. Each table is read whole by the
        app at its revision, the same rows and value in each; the numbers are the tables' own, never the model's."""
        from . import measurement_charts as charts

        title = " ".join(str(args.get("title") or "").split())
        if not 1 <= len(title) <= 90:
            return {"error": "give the chart a title of 1-90 characters"}, None
        tables = args.get("tables")
        if not isinstance(tables, list) or not 1 <= len(tables) <= charts.TABLES_MAX:
            return {"error": f"a means chart names tables: one to {charts.TABLES_MAX} session entries "
                             "({id, revision}), one bar each, e.g. one per run"}, None
        names = args.get("names")
        if names is not None and (not isinstance(names, list) or len(names) != len(tables)
                                  or not all(isinstance(n, str) and n.strip() for n in names)):
            return {"error": "names are one short name per table, in the same order (e.g. Run 1, Run 2)"}, None
        only = args.get("only") if isinstance(args.get("only"), str) and args.get("only") else None
        unit = " ".join(str(args.get("unit") or "").split())[:20]
        sets = []
        for n, table in enumerate(tables):
            document, revision, why = await self._table(table)
            if document is None:
                return {"error": f"table {n + 1}: {why}"}, None
            try:
                sets.append(await asyncio.to_thread(
                    charts.series, document, revision=revision, rows=str(args.get("rows") or ""),
                    value=str(args.get("value") if args.get("value") is not None else ""), order="table", only=only))
            except ValueError as exc:
                return {"error": f"table {n + 1} ({(table or {}).get('id')}): {exc}"}, None
        names = [" ".join(n.split())[:16] for n in names] if names else [f"Run {i + 1}" for i in range(len(sets))]
        png = await asyncio.to_thread(charts.means_png, sets, names, title, unit)
        content = charts.means_content(sets, names, title, unit)
        rel = self._save_output("chart", png)
        across = content["acrossTables"]
        caption = (f"Chart of the mean {sets[0].column} of {len(sets)} tables (bar: mean, whisker: one standard deviation), "
                   "drawn by the app from " + ", ".join(str(s.source.get("title") or s.source.get("id")) for s in sets)
                   + "; its values are the tables' own")
        self.emit_output("image", rel, caption, content, generated=False,
                         derived={"from": "tables", "source": dict(sets[0].source),
                                  "sources": [dict(s.source) for s in sets], "chart": charts.MEANS, "animated": False})
        self.step(f"Drew a chart of the mean {sets[0].column} of {len(sets)} saved tables")
        return ({"ok": True, "output": rel, "tables": [{k: t[k] for k in ("name", "n", "mean", "sd", "sem", "median", "min",
                                                                             "max", "excluded")} | {"id": t["source"].get("id")}
                                                          for t in content["tables"]],
                 "acrossTables": across, "unit": unit or None,
                 "note": "the chart is attached; the user sees it with your result. Report these numbers as they are"},
                await asyncio.to_thread(_as_jpeg, png))

    async def tool_make_chart(self, args: dict):
        from . import measurement_charts as charts

        if args.get("kind") == charts.MEANS:
            return await self._chart_means(args)
        series, title, kind, error = await self._chart_series(args)
        if error is not None:
            return error, None
        png = await asyncio.to_thread(charts.chart_png, series, title, kind)
        rel = self._chart_output(series, title, kind, png, animated=False)
        self.step(f"Drew a chart of {series.column} from {series.n} rows of a saved table")
        return ({"ok": True, "output": rel, "rows": series.n, "excluded": series.excluded, "stats": series.stats(),
                 "note": "the chart is attached; the user sees it with your result"}, await asyncio.to_thread(_as_jpeg, png))

    async def tool_animate_measurements(self, args: dict):
        """The chart built row by row; when it can't be animated here, the chart instead, said so (never nothing)."""
        from . import measurement_charts as charts

        series, title, kind, error = await self._chart_series(args)
        if error is not None:
            return error, None
        still = await asyncio.to_thread(charts.chart_png, series, title, kind)
        why = charts.ready()
        if not why:
            try:
                moving, frames = await asyncio.to_thread(charts.animation_png, series, title, kind,
                                                         float(args.get("seconds") or 6))
            except Exception as exc:  # noqa: BLE001 - said, and the chart is made instead
                why = f"drawing its frames failed ({type(exc).__name__})"
        if why:
            note = f"It couldn't be animated here ({why}), so this is the chart instead"
            rel = self._chart_output(series, title, kind, still, animated=False, note=note)
            self.step(f"Couldn't animate the table ({why}); drew the chart instead", "warning")
            return ({"ok": True, "output": rel, "animated": False, "rows": series.n, "stats": series.stats(),
                     "note": f"{note}: say so in your result, in those words"}, await asyncio.to_thread(_as_jpeg, still))
        rel = self._chart_output(series, title, kind, moving, animated=True, frames=frames)
        self.step(f"Animated {series.column} from {series.n} rows of a saved table")
        return ({"ok": True, "output": rel, "animated": True, "frames": frames, "rows": series.n,
                 "excluded": series.excluded, "stats": series.stats(),
                 "note": "its last frame (the whole chart) is attached; the user sees it play with your result"},
                await asyncio.to_thread(_as_jpeg, still))

    async def tool_make_page(self, args: dict):
        """A rendered HTML page the researcher wrote itself (slides or a small interactive visual), kept as one of its
        outputs and served under the research files' no-network sandbox (orchestrator/research/files.py): the studio
        shows it rendered in the researcher's Tab or the reader, never as text."""
        kind = "presentation" if args.get("kind") == "presentation" else "visual"
        self.pages_made = getattr(self, "pages_made", 0) + 1
        title = whole(args.get("title") or ("Presentation" if kind == "presentation" else "Visual"), "the page's title")
        summary = whole(args.get("summary"), "the summary")
        html = clean_lesson(str(args.get("html") or ""))
        if not html or "</html>" not in html.lower():
            return {"error": "give the whole HTML document in html, from <!doctype html> to </html>"}, None
        if len(html) > PAGE_MAX_CHARS:
            return {"error": f"the page is {len(html):,} characters; keep it under {PAGE_MAX_CHARS:,}"}, None
        data_from = [str(x)[:300] for x in (args.get("data_from") or []) if str(x).strip()][:20]
        self.outputs += 1
        rel = f"outputs/{self.outputs:02d}-{slug(kind + '-' + title)}.html"
        (self.dir / rel).write_text(html)
        content = {"kind": kind, "title": title, "summary": summary, "data_from": data_from}
        what = "Presentation" if kind == "presentation" else "Interactive visual"
        self.emit_output("html", rel, f"{what}: {title}", content, generated=True,
                         contentKind="report" if kind == "presentation" else "visual")
        self.step(f"Made {'a presentation' if kind == 'presentation' else 'an interactive visual'}: {title}")
        return {"ok": True, "output": rel, "note": "kept with your work; the user opens it rendered. Now finish, naming "
                                                   "it in your summary."}, None

    async def tool_make_video(self, args: dict):
        """Gemini Omni 1.1 Flash image-to-video from the photo, padded to 16:9 (research_worker/video.py; spike S6 made it
        with Veo 3.1 Fast, which Google retired on 22 Oct 2026). When the provider refuses or fails, the error says
        which model, why in a person's words and what to do (api_usage.refusal: retired, out of credit, no key, rate
        limited...), with the provider's own status beside it, so the user hears why (live 2026-10-28: "I can't see the
        exact error"; Will's try-out of 29 Oct: "retired", said without the reason), and what can be made instead."""
        from google import genai

        from .fidelity import pad_to

        seconds = int(args.get("seconds") or 6)
        seconds = seconds if seconds in video.SECONDS else 6
        # Under a spending limit, the length asked when it fits what is left, else the longest that does: the video the
        # user asked for is made shorter, never refused for a length the app chose (Will, 30 Oct).
        asked = seconds
        fits = [s for s in video.SECONDS if s <= asked and (
            self.budget.unlimited or self.budget.spent + PRICES[video.ENTRY] * s <= self.budget.effort.cap_usd)]
        seconds = max(fits) if fits else asked  # none fits: over the limit, said as such (budget.check)
        price = PRICES[video.ENTRY] * seconds
        self.budget.check(price)
        source = self.crop_jpeg or self.frame_jpeg
        motion = whole(" ".join(str(args.get("motion") or "").split()), "motion")
        self.step(f"Making a {seconds} s video: {motion[:80]}")
        # From the photo when there is one; with no camera (or nothing in view) it is made from the words alone.
        prompt = (f"A still camera looks through a light microscope at the specimen in this photo. {motion} "
                  "Keep the camera and framing still; no text, no labels." if source else
                  f"A short scientific illustration video, as seen through a light microscope. {motion} "
                  "Keep the camera and framing still; no text, no labels.")
        try:
            client = genai.Client(http_options={"retry_options": {"attempts": 1}})  # one admitted attempt
            data = await video.make(client, prompt, pad_to(source, 1280, 720) if source else None, seconds)
        except asyncio.CancelledError:
            # Asked for, the video is made and billed whether or not anyone waits for it: its price is counted.
            self.cancelled_call(VIDEO_MODEL, usd=price)
            raise
        except Exception as exc:  # noqa: BLE001 - why is said, never hidden behind "a step didn't work"
            return self._video_unavailable(exc, detail=f"{type(exc).__name__}: {exc}"), None
        self.spend(VIDEO_MODEL, price)
        self.outputs += 1
        rel = f"outputs/{self.outputs:02d}-video.mp4"
        (self.dir / rel).write_bytes(data)
        if not await asyncio.get_running_loop().run_in_executor(
                None, mark_video, self.dir / rel, ILLUSTRATION, GENERATED, VIDEO_MODEL):
            self.step("Could not burn the AI-illustration label into the video; the studio still says it", "warning")
        caption = (f"Generated illustration ({VIDEO_MODEL}), starting from your photo; not footage of your slide" if source
                   else f"Generated illustration ({VIDEO_MODEL}); not footage of your slide")
        self.emit_output("video", rel, caption, text_content(motion, caption), generated=True)
        return {"ok": True, "output": rel, **({"seconds": seconds, "note": f"made {seconds} s long to fit the spending limit"}
                                              if seconds != asked else {})}, None

    def _video_unavailable(self, error, detail: str) -> dict:
        """The video model refused or failed: said on its card with why and what to do (api_usage.refusal) and the
        provider's status, kept on the run as a fact its result carries (`unavailable`, whatever its write-up says), and
        told to the model with what it can make instead (an animation of a table it read, when it has that tool)."""
        found = refusal(error, model=VIDEO_NAME, provider="Google", keys=("GEMINI_API_KEY", "GOOGLE_API_KEY"))
        status = provider_status(error)
        self.unavailable = [*getattr(self, "unavailable", []),
                            {"model": VIDEO_NAME, "for": "the video", **found, "status": status[:200]}]
        self.step(f"The video model ({VIDEO_NAME}) couldn't be used: {refusal_clause(found)}; {found['todo']}; no video "
                  "was made",
                  "warning", detail=f"make_video: {status}; {detail}"[:600])
        instead = (" To show measurements changing instead, animate_measurements draws an animation from a table this "
                   "session kept." if "animate_measurements" in self.tool_names() else "")
        return {"error": f"the video model ({VIDEO_NAME}) couldn't be used: {refusal_clause(found)} ({status}); no "
                         "video was made. Say that in your result's limitations, "
                         f"with what the user can do: {found['todo']}.{instead}",
                "reason": found["reason"], "todo": found["todo"]}

    def _media_unavailable(self, error, model: str, what: str, keys: tuple[str, ...]) -> dict:
        """A picture model refused or failed (out of credit, no key, retired, its own refusal...): said as the video's is,
        on the card with why and the one thing to do, kept on the run's result (`unavailable`) and told to the model,
        never a raw provider error or "one of its steps didn't work" (Will, 30 Oct: no ability error in the demo)."""
        name = IMAGE_NAMES.get(model, model)
        found = refusal(error, model=name, keys=keys)
        status = provider_status(error)
        self.unavailable = [*getattr(self, "unavailable", []),
                            {"model": name, "for": what, **found, "status": status[:200]}]
        self.step(f"The picture model ({name}) couldn't be used: {refusal_clause(found)}; {found['todo']}; no picture "
                  "was made", "warning", detail=f"{model}: {status}; {type(error).__name__}: {error}"[:600])
        return {"error": f"the picture model ({name}) couldn't be used: {refusal_clause(found)} ({status}); no picture "
                         f"was made. Say that in your result's limitations, with what the user can do: {found['todo']}.",
                "reason": found["reason"], "todo": found["todo"]}

    async def tool_make_lesson(self, args: dict):
        """A one-page interactive lesson: Claude when ANTHROPIC_API_KEY is set, else gpt-6.1-sol."""
        title = whole(args.get("title") or "Lesson", "the lesson's title")
        extra = whole(args.get("outline"), "the outline")
        for part in ("key_ideas", "questions"):
            if len(args.get(part) or []) > LESSON_MAX:
                return {"error": f"one lesson holds at most {LESSON_MAX} {part.replace('_', ' ')}; give the {LESSON_MAX} that matter most"}, None
        content = lesson_content(title, args.get("key_ideas"), args.get("questions"), extra)
        if not content["steps"]:
            return {"error": "give the quiz questions with their answers (questions)"}, None
        self.step(f"Writing a lesson: {title}")
        content, check = await self._check_lesson(content)
        if self.spec.get('independent_presentation'):
            self.lesson_draft = {**content, 'interactive':whole(args.get('interactive'), 'interactive'),
                                 'outline':extra, 'learner':self.learner_text(), **({'check':check} if check else {})}
            self.step('Lesson content prepared; HTML will be produced independently after findings are saved')
            # Said as kept, never "pending" or "queued": a model told the app's state wrote it into the lesson's
            # limitations (2026-10-11, "The lesson is queued; its on-screen display is not yet confirmed").
            return {'ok':True, 'kept':'the lesson', 'content':content,
                    'next':'The lesson is kept with your work and shown with your findings. Finish your findings now; '
                           'they are about the specimen and the evidence, not about the lesson or the app.'}, None
        rel = await self._lesson_page(content, whole(args.get("interactive"), "interactive"), extra, check=check)
        if rel is None:
            return {"error": "the lesson came back empty"}, None
        # What the check found is its to know too (steps a model answered without the picture).
        return {"ok": True, "output": rel, **({"check": check} if check else {})}, None

    async def _check_lesson(self, content: dict) -> tuple[dict, dict | None]:
        """W6-3 (lesson_check.py): a cheap model that sees no picture answers each step, and steps
        it gets right are rewritten once, with the photo, to need it (option LESSON_CHECK
        "rewrite"; "check" only logs). Returns the content to write and the check's record (None
        when no check ran). A check that fails or has no budget left leaves the lesson as it is."""
        mode = self.spec.get("lesson_check") or "off"
        steps = content.get("steps") or []
        script = (self.spec.get("fake") or {}).get("lesson_check")
        if mode not in ("check", "rewrite") or not steps or (self.offline and script is None):
            return content, None
        record = {"mode": mode, "model": "fake" if self.offline else CHECK_MODEL, "steps": len(steps),
                  "answered_without_picture": [], "rewritten": []}
        started = time.monotonic()
        try:
            self.budget.check(CHECK_ESTIMATE_USD)
            if self.offline:
                self.spend("fake", 0.001)
                right = [s["id"] for s in steps if s["id"] in (script.get("right") or [])]
            else:
                answers = (await fast_json(blind_prompt(content), self)).get("answers")
                if not isinstance(answers, dict):  # no answers to grade: the check failed, it did not find "0 right"
                    raise UnreadableReply("the blind answers came back without answers")
                right = right_ids(content, await fast_json(grade_prompt(content, answers), self))
        except OverBudget:
            self.step("Lesson check skipped: not enough budget left for it", "warning")
            return content, None
        except Exception as exc:  # noqa: BLE001 - the lesson is written as it is
            self.step("The lesson check didn't work; the lesson is written as it is", "warning",
                      detail=f"{type(exc).__name__}: {exc}"[:600])
            return content, None
        record["answered_without_picture"] = right
        record["check_s"] = round(time.monotonic() - started, 1)
        self.step(f"Lesson check: {len(right)} of {len(steps)} questions answered right without the picture"
                  + (f" ({', '.join(right)})" if right else ""), "lesson-check")
        photo = self.crop_jpeg or self.frame_jpeg
        if mode != "rewrite" or not right:
            return content, record
        if not photo:
            self.step("No photo to make them need; the questions stay as they are", "lesson-check")
            return content, record
        try:
            self.budget.check(REWRITE_ESTIMATE_USD)
            if self.offline:
                self.spend("fake", 0.001)
                new = script.get("rewrite") or {}
            else:
                new = await edit_json(rewrite_prompt(content, right), self, image=photo)
        except OverBudget:
            self.step("No budget left to rewrite them; the questions stay as they are", "warning")
            return content, record
        except Exception as exc:  # noqa: BLE001
            self.step("Rewriting them didn't work; the questions stay as they are", "warning",
                      detail=f"{type(exc).__name__}: {exc}"[:600])
            return content, record
        edited, changed = edited_lesson(content, new, change_text(right))
        record["rewritten"] = [i for i in changed if i in right]
        record["rewrite_s"] = round(time.monotonic() - started - record["check_s"], 1)
        self.step(f"Rewrote {', '.join(record['rewritten'])} to need the picture" if record["rewritten"]
                  else "The rewrite changed nothing; the questions stay as they are", "lesson-check")
        return edited, record

    async def _lesson_page(self, content: dict, interactive: str = "", extra: str = "", note: str = "",
                           check: dict | None = None) -> str | None:
        """Write the lesson's page from its content (offline: offline_lesson) and emit it."""
        title = content["title"]
        outline = "\n".join(
            ["Key ideas:"] + [f"- {i}" for i in content["key_ideas"]]
            + ([f"Interactive idea: {interactive}"] if interactive else [])
            + ["Quiz:"] + [f"Question {n} (id=\"{s['id']}\"{STEP_MARKS.get(step_kind(s), '')}): {s['prompt']}\n  Answer: {s['answer']}"
                           + (f"\n  Hint: {s['hint']}" if s["hint"] else "") for n, s in enumerate(content["steps"], 1)]
            + ([f"Also: {extra}"] if extra else []))
        self.budget.check(CALL_MOST["lesson"])
        prompt = LESSON.format(title=title, outline=outline, asked=" ".join(self.brief().split()),
                               photo="../inputs/crop.jpg" if self.crop_jpeg else "../inputs/frame.jpg",
                               learner=self.learner_text())
        if self.offline:
            html, model = offline_lesson(content), "fake-lesson"
        else:
            writer = lesson_writer()  # priced as the model writing it, should it be cut off or fail
            out, most = self.bound(writer, prompt, ceiling=LESSON_MAX_OUTPUT)  # a limited run: sized to what is left
            async with self.sent(writer, prompt, most=most):
                html, model = await write_lesson(prompt, self, **({"out": out} if out else {}))
        html = clean_lesson(html)
        if not html:
            return None
        html = inline_photo(html, self.crop_jpeg or self.frame_jpeg)
        self.outputs += 1
        rel = f"outputs/{self.outputs:02d}-{slug('lesson-' + title)}.html"
        (self.dir / rel).write_text(html)
        self.emit_output("html", rel, f"Interactive lesson: {title} (written by {model}{note})", content, generated=True,
                         **({"check": check} if check else {}))
        return rel

    # --- revising: a new version of one thing it made (ResearchManager.followup "revise") ----
    async def revise(self) -> None:
        """Make a new version of the target output with the user's change. Each kind has its
        own fixed steps (no model choosing tools): see the manager's followup()."""
        kind, change = self.revising.get("kind"), " ".join(str(self.revising.get("change") or "").split())
        target = self.revising.get("target") or {}
        script = (self.spec.get("fake") or {}).get("revise") or {}
        if self.offline and script.get("seconds"):
            await asyncio.sleep(float(script["seconds"]))
        self.take_notes()
        if kind == "lesson":
            await self._revise_lesson(target, change, script)
        elif kind == "labelled picture":
            await self._revise_labels(target, change, script)
        elif kind == "illustration":
            await self._revise_illustration(target, change)
        elif kind == "video":
            await self._revise_video(target, change)
        else:
            raise RuntimeError(f"cannot revise a {kind}")

    async def _revise_lesson(self, target: dict, change: str, script: dict) -> None:
        old = target.get("content") or {}
        if not old.get("steps"):
            raise RuntimeError("the lesson has no steps to change")
        self.step(f"Revising the lesson: {change[:120]}")
        if self.offline:
            self.spend("fake", 0.001)
            named = named_steps(change, len(old["steps"]))  # scripted edit: the named steps (or all) get the change added
            new = script.get("lesson") or {**old, "steps": [{**s, "prompt": f"{s['prompt']} ({change})"}
                                                             if not named or s["id"] in named else s for s in old["steps"]]}
        else:
            self.budget.check(CALL_MOST["lesson edit"])
            new = await edit_json(editing_prompt(REVISE_LESSON, change, "content",
                                                 {k: old.get(k) for k in ("title", "key_ideas", "steps")}, 8000), self)
        content, changed = edited_lesson(old, new, change)
        anchor = str(target.get('anchor') or '')
        if anchor.startswith('step:'):
            step_id = anchor.removeprefix('step:')
            index = next((i for i, step in enumerate(old['steps'], 1) if step['id'] == step_id), None)
            if index is None:
                raise RuntimeError('the selected lesson step is unavailable')
            content, changed = edited_lesson(old, new, f'question {index}', only={step_id})
        fields = target.get('edit_fields')
        if fields is not None:
            if (not isinstance(fields, list) or not fields
                    or any(field not in ('prompt', 'answer', 'hint') for field in fields)):
                raise RuntimeError('invalid retained lesson edit scope')
            edited = {step['id']:step for step in content['steps']}
            content = {**old, 'steps':[{**step, **{field:edited[step['id']][field] for field in fields
                if step['id'] in edited and field in edited[step['id']]}} for step in old['steps']]}
            changed = [step['id'] for step, previous in zip(content['steps'], old['steps']) if step != previous]
        if not changed:
            raise RuntimeError("the edit changed nothing")
        if self.spec.get('independent_presentation'):
            self.lesson_draft = content
            self.step('Lesson content revised; its presentation will be produced independently')
            return
        self.step("Changed " + ", ".join(changed[:8]) + "; writing the page again")
        rel = await self._lesson_page(content, extra=f"The student asked for this change from the earlier version: {change}",
                                      note=f"; version with: {change[:60]}")
        if rel is None:
            raise RuntimeError("the lesson came back empty")

    async def _revise_labels(self, target: dict, change: str, script: dict) -> None:
        old = [dict(l) for l in (target.get("content") or {}).get("items") or []]
        source = self.crop_jpeg or self.frame_jpeg
        # The model reads the change (a rename, a removal, both, or something new); no word pattern decides what was
        # meant. One small edit call returns the whole new list.
        if self.offline:
            self.spend("fake", 0.001)
            labels = [dict(l) for l in script.get("labels") or old]
        else:
            self.budget.check(CALL_MOST["label edit"])
            reply = await edit_json(editing_prompt(REVISE_LABELS, change, "labels", old), self)
            labels = [l for l in reply.get("labels") or [] if isinstance(l, dict) and str(l.get("name") or "").strip()]
            if len(labels) > LABELS_MAX:
                self.step(f"The edit gave {len(labels)} labels; the first {LABELS_MAX} fit on the picture", "warning")
                labels = labels[:LABELS_MAX]
        if not labels:
            raise RuntimeError("no labels would be left")
        # A fact, checked: every label it kept sits where one already was (only renamed or removed), so the app
        # draws them itself on the photo; anything new or moved is labelled again by the picture model.
        boxes = [tuple(l["box"]) for l in old if isinstance(l.get("box"), list)]
        plain = len(labels) <= len(old) and all(isinstance(l.get("box"), list) and tuple(l["box"]) in boxes for l in labels)
        if plain:
            self.step(f"Changing the labels as asked: {change}")
        drawn = draw_labels(source, labels) if plain and source else None
        if drawn is None:
            # Re-annotated on the original photo with the edited list (checked for redrawing).
            result, _ = await self.tool_annotate_image({"labels": labels})
            if result.get("error"):
                raise RuntimeError(result["error"])
            return
        rel = self._save_output("annotated", drawn, (LABELLED, COMPOSITE, "labels drawn by the app"))
        names = ", ".join(str(l["name"]) for l in labels)
        self.emit_output("image", rel, f"Labels drawn by the app on the microscope photo: {names}", labels_content(labels),
                         generated=False, altered=False, fidelity=0.0)

    def _target_bytes(self, target: dict) -> bytes:
        path = (self.dir / str(target.get("path") or "")).resolve()
        if not path.is_relative_to(self.dir.resolve()) or not path.is_file():
            raise RuntimeError("the current version's file is missing")
        clean = path.with_suffix(".clean.png")  # the picture without its AI label (_save_output)
        return clean.read_bytes() if path.suffix == ".png" and clean.is_file() else path.read_bytes()

    async def _revise_illustration(self, target: dict, change: str) -> None:
        current = self._target_bytes(target)  # the version the user is looking at, edited
        self.step(f"Editing the illustration: {change[:120]}")
        made = await self._image_call("illustration", f"Edit this scientific illustration: {change}. Keep everything else "
                                      "as it is.", current)
        if "error" in made:
            raise RuntimeError(made["error"])
        rel = self._save_output("illustration", made["png"], (ILLUSTRATION, GENERATED, made["model"]))
        caption = f"Generated illustration ({made['model']}), not the specimen; changed: {change[:80]}"
        before = str((target.get("content") or {}).get("text") or "")
        self.emit_output("image", rel, caption, text_content(f"{before} Then changed: {change}".strip(), caption), generated=True)

    async def _revise_video(self, target: dict, change: str) -> None:
        text = str((target.get("content") or {}).get("text") or "")
        caption = str(target.get("caption") or "")
        motion = text[: -len(f" ({caption})")] if caption and text.endswith(f" ({caption})") else text
        motion = f"{motion.strip()} {change}".strip()
        if not self.offline:
            result, _ = await self.tool_make_video({"motion": motion, "seconds": 6})
            if result.get("error"):
                raise RuntimeError(result["error"])
            return
        data = self._target_bytes(target)  # scripted: the same clip again, as the new version
        self.spend("fake-video", 0.001)
        self.outputs += 1
        rel = f"outputs/{self.outputs:02d}-video.mp4"
        (self.dir / rel).write_bytes(data)
        caption = "Generated illustration (fake-video), starting from your photo; not footage of your slide"
        self.emit_output("video", rel, caption, text_content(motion, caption), generated=True)

    async def tool_plan(self, args: dict):
        """What a deep run will look up first (W6-4d): shown and read to the user, who may steer it
        with a note (researcher_note). It carries on at once; nothing waits for a reply."""
        searches = [" ".join(str(q).split()) for q in args.get("searches") or [] if str(q).strip()]
        sources = " ".join(str(args.get("sources") or "").split())
        if not searches:
            return {"error": "give the searches or questions you will start with"}, None
        self.plan = {"searches": searches, "sources": sources}
        self.step("Plan: " + "; ".join(searches) + (f" (sources: {sources})" if sources else ""), "plan")
        self.emit("plan", searches=searches, sources=sources)
        return {"ok": True, "note": "shown and read to the user; carry on now, and keep to any note they send"}, None

    async def tool_request_evidence(self, args: dict):
        """Asks the user for evidence it lacks (UX §2: "I need 15 seconds at this field. Record it?"): said to
        the user once (announcer.research_request); the user decides. Nothing waits: it carries on, an answer
        comes as a note, and what stays missing is a limitation of its finding."""
        if self.requests_made >= MAX_REQUESTS:
            return {"error": f"{MAX_REQUESTS} requests is the most in one run: carry on with what you have"}, None
        what = " ".join(str(args.get("what") or "").split())
        why = " ".join(str(args.get("why") or "").split())
        if not what or not why:
            return {"error": "say what you need and what it would settle"}, None
        try:
            seconds = max(0, min(HOLD_MAX_S, int(args.get("seconds") or 0))) or None
        except (TypeError, ValueError):
            seconds = None
        self.requests_made += 1
        self.asked += 1
        request_id = f"q{self.requests_from + self.asked}"
        self.evidence_ids.append(request_id)
        self.step(f"Asked the user for {what} ({why})", "request")
        self.emit("request", id=request_id, what=what, why=why, seconds=seconds)
        return {"ok": True, "id": request_id,
                "note": "asked the user; carry on with what you have now. Their answer, if any, comes as a note (a "
                        "finish waits a little for it); if it never comes, say in finish.limitations what stayed "
                        "unresolved"}, None

    # --- the builder (deliverable "adapter"; orchestrator/drafts.py does what touches the Mac) -------------------------
    async def tool_read_source(self, args: dict):
        """A repository's file list, or one file's raw text (public addresses only, re-checked redirects), untrusted."""
        import httpx

        from .safe_fetch import FetchRefused, _get, check_url

        url, path = str(args.get("url") or "").strip(), str(args.get("path") or "").strip().lstrip("/")
        from . import source_repository

        self.step(f"Reading {path or 'its files'}", detail=url)
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False,
                                         headers={"User-Agent": "ObservationUI builder"}) as client:
                async def get(target):
                    check_url(target)
                    return await _get(client, target, accept_image=False)

                pins = getattr(self, "source_pins", {})
                target, text, revision = await source_repository.read(url, path, pins, get)
                self.source_pins = pins
                (self.dir / "source-pins.json").write_text(json.dumps(pins, indent=2))
        except (FetchRefused, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return {"error": f"couldn't read it: {exc}"}, None
        self.builder_work.add(("read", target, hashlib.sha256(text.encode()).hexdigest()))
        return {"url": target, "revision": revision, "text": block(text, tag="source", source="web", limit=60_000)}, None

    async def tool_draft(self, args: dict):
        fields = {k: args[k] for k in ("name", "what", "licence", "tier", "makers", "paper", "imports", "tags") if args.get(k)}
        reply = await self.request("draft_write", rid=self.rid, adapter=str(args.get("adapter") or ""),
                                   requirements=str(args.get("requirements") or ""), fields=fields, timeout=30)
        self.builder_work.add(("draft", hashlib.sha256(json.dumps([args.get("adapter"), args.get("requirements")],
                                                                  default=str).encode()).hexdigest()))
        self.step("Drafted its adapter" if reply.get("ok") else "Its draft was refused", None if reply.get("ok") else "warning",
                  detail=str(reply.get("error") or ", ".join(reply.get("requirements") or []))[:300])
        return reply, None

    async def tool_pin_file(self, args: dict):
        url = str(args.get("url") or "")
        self.step("Getting a file it needs", detail=url)
        reply = await self.request("draft_pin", rid=self.rid, url=url, timeout=1800)
        self.builder_work.add(("pin", url, bool(reply.get("ok"))))
        return reply, None

    async def tool_try_draft(self, args: dict):
        self.step("Trying it on the draft's retained input, inside the sandbox")
        reply = await self.request("draft_try", rid=self.rid, timeout=1800)
        self.builder_work.add(("try", hashlib.sha256(json.dumps(reply, sort_keys=True, default=str).encode()).hexdigest()))
        self.step("It ran the selected job" if reply.get("ok") else "It didn't run yet",
                  None if reply.get("ok") else "warning", detail=str(reply.get("error") or reply.get("says") or "")[:300])
        return reply, None

    async def tool_record_finding(self, args: dict):
        text = str(args.get("text") or "")
        sources = [u for u in args.get("sources") or [] if isinstance(u, str)]
        self.recorded.append({"kind": args.get("kind") or "fact", "text": text, "sources": sources})
        self.emit("finding", kind=args.get("kind") or "fact", text=text, sources=sources)
        return {"ok": True}, None

    async def tool_propose_correction(self, args: dict):
        self.step(f"Suggests a correction: {args.get('to_label')}", "correction")
        self.emit("correction", object_id=args.get("object_id") or ((self.spec.get("snapshot") or {}).get("object") or {}).get("id"),
                  to_label=str(args.get("to_label") or ""), reason=str(args.get("reason") or ""),
                  confidence=args.get("confidence"), sources=list(args.get("sources") or []))
        return {"ok": True, "note": "proposed; the user decides"}, None

    def _inbox_lines(self) -> list[dict]:
        try:
            text = (self.dir / "inbox.jsonl").read_text()
        except OSError:
            return []
        out = []
        for line in text[: text.rfind("\n") + 1].splitlines():
            try:
                note = json.loads(line)
            except ValueError:
                note = None
            out.append(note if isinstance(note, dict) else {})
        return out

    def waiting_on(self) -> list[str]:
        """What this run still waits on from the user: its questions not answered yet (q1...), and footage they
        agreed to that is still being held for it (k...), from the app's own marks on this run's notes."""
        notes = self._inbox_lines()[self.inbox_from:]
        answered = {n.get("answers") for n in notes}
        held = {n.get("held") for n in notes}
        mine = list(getattr(self, "evidence_ids", []))
        asked = [q for q in mine if q not in answered]
        holding = [n["holding"] for n in notes if isinstance(n.get("holding"), str) and n["holding"] not in held]
        return [x for x in asked + holding if x not in self.waited]

    async def wait_for_the_user(self) -> dict | None:
        """Before it finishes: while one of its questions is unanswered, or footage the user agreed to is still being
        held, the finish waits for it (ANSWER_WAIT_S at most, once each), whatever the clock says, unless it was made to
        write up (engines._final: no round after it could read the answer). Returns what to tell the model when
        something came (it finishes after reading it), else None."""
        pending = self.waiting_on()
        script = self.spec.get("fake")
        most = float(script.get("answer_wait") or 0.0) if isinstance(script, dict) else ANSWER_WAIT_S
        if not pending or most <= 0 or self.final_round:
            return None
        self.waited.update(pending)
        wait = most  # no run clock to keep time for: a finish waits its ANSWER_WAIT_S for the answer
        self.step(f"Waiting up to {wait:.0f} s for the user ({', '.join(pending)}) before finishing", "waiting")
        seen = len(self._inbox_lines())
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            if len(self._inbox_lines()) > seen:  # something came: read it next round, then finish
                return {"ok": False, "not_finished": "the user's answer came while you were finishing; it is the "
                                                     "next note. Use it, then call finish again."}
        self.step(f"No answer came in {wait:.0f} s: finishing without it", "waiting")
        return None

    async def tool_save_findings(self, args: dict):
        """Persist completed science before generating a fallible presentation."""
        if not args.get('report'):
            return {'error': 'save_findings requires a full report'}, None
        previous = self.result
        try:
            reply, image = await self.tool_finish(args, interim=True)
            if reply.get('error'):
                return reply, image
            if self.result.get('evidence'):
                await self.check_evidence()
            if self.result.get('claims'):
                await self.check_claims()
            if callable(getattr(self, 'request', None)):
                saved = await self.request('save_findings', rid=self.rid, runId=self.spec.get('run_id'), result=self.result)
                if not saved.get('ok'):
                    return {'error': saved.get('error') or 'Full findings could not be saved; do not claim they were retained'}, None
            else:
                # In-process deterministic harnesses synchronously persist through this callback.
                self.emit('findings', result=self.result)
            self.saved_finding_refs.update(ref['path'] for ref in self.result.get('refs', []))
            return {'ok': True, 'saved': 'Full findings retained; presentation can now be created independently'}, None
        finally:
            self.result = previous  # engines stop only on the final finish call

    def malformed_call(self, name: str, raw, exc: Exception) -> dict:
        """A tool call whose arguments were not valid JSON: never run as an empty call. What it sent is kept (a
        finish's, as a rejected part) and the model is told why, so it can send it again."""
        error = f"the arguments were not valid JSON ({exc})"
        self.step("A step came back unreadable; it is told to send it again", "warning", detail=f"{name}: {error}")
        if name == "finish":
            self.rejected.append({"part": "finish", "error": error, "submitted": str(raw or "")})
        return {"error": f"{error}; call {name} again with valid JSON"}

    async def finish_from_text(self, text: str) -> None:
        """The model answered in plain text instead of calling finish: the text is its summary, said to be so, and
        the outcome is partial (tool_finish), never a plain "done"."""
        text = (text or "").strip()
        self.without_finish = True
        self.final_round = True  # nothing will come after this
        await self.call_tool("finish", {"title": self.spec.get("label") or "Research", "summary": text,
                                        "one_liner": text.split(". ")[0], "significance": "low"})

    def _parts(self, args: dict) -> tuple[dict, list[dict]]:
        """Each optional part of a finish checked on its own: (the parts that passed, the ones that did not, each
        with what was sent and why)."""
        from .results import comparison_tables, full_report
        from .charts import resolve

        checks = {"tables": lambda v: comparison_tables(v), "report": lambda v: full_report(v),
                  "charts": lambda v: resolve(v, getattr(self, 'session_documents', {}))}
        kept, rejected = {}, []
        for part, check in checks.items():
            try:
                kept[part] = check(args.get(part))
            except ValueError as exc:
                rejected.append({"part": part, "error": str(exc), "submitted": json.dumps(args.get(part), ensure_ascii=False)})
        # Its own words are kept whole (limits.py): one over the limit is refused, said, and kept as it was sent.
        for part in ("title", "summary", "one_liner", "look_for", "details", "limitations", "next_questions"):
            value = args.get(part)
            texts = value if isinstance(value, list) else [value]
            if any(len(str(t or "")) > FIELD_MAX for t in texts):
                rejected.append({"part": part, "error": f"longer than {FIELD_MAX:,} characters",
                                 "submitted": json.dumps(value, ensure_ascii=False)})
        return kept, rejected

    async def tool_finish(self, args: dict, *, interim: bool = False):
        parts, rejected = self._parts(args)
        # Nothing written at all: no summary, report, table or note (an empty finish is never a result).
        empty = (not str(args.get("summary") or "").strip() and parts.get("report") is None and not parts.get("tables")
                 and not any(str(d or "").strip() for d in args.get("details") or []))
        if (rejected or empty) and (interim or not getattr(self, "final_round", False)):
            # Rounds and time remain: it fixes what it sent (nothing is kept from this call yet).
            said = [f"{r['part']}: {r['error']}" for r in rejected] + (["it needs a summary, a report, a table or details"] if empty else [])
            return {"error": "; ".join(said)}, None
        if empty:
            # Its last chance and nothing written: no result from this call (the run keeps its findings: run()).
            self.rejected = [*getattr(self, "rejected", []),
                             {"part": "finish", "error": "nothing written", "submitted": json.dumps(args, ensure_ascii=False)}]
            return {"error": "finish needs a summary, a report, a table or details"}, None
        # The finish that is kept is what counts: an earlier malformed one it has since sent again is not "lost".
        self.rejected = rejected
        tables, report, charts = parts.get("tables"), parts.get("report"), parts.get("charts")
        came = await self.wait_for_the_user() if not interim else None
        if came is not None:
            return came, None
        keep = [self.refs[r] for r in (self._ref_key(u) for u in args.get("refs") or []) if r in self.refs]
        bad = {r["part"] for r in rejected}

        def text(part: str) -> str:  # whole, or nothing when over the limit (kept in `rejected` as sent)
            return "" if part in bad else str(args.get(part) or "").strip()

        def texts(part: str) -> list[str]:
            return [] if part in bad else [str(x).strip() for x in args.get(part) or [] if isinstance(x, str) and x.strip()]

        self.result = {
            "title": text("title"),
            "summary": text("summary"),
            "one_liner": text("one_liner"),
            "significance": args.get("significance") if args.get("significance") in ("none", "low", "high") else "low",
            "details": texts("details"),
            "look_for": text("look_for"),
            "confidence": args.get("confidence") if isinstance(args.get("confidence"), (int, float)) else None,
            "sources": [s for s in (args.get("sources") or []) if isinstance(s, str) and s.startswith("http")],
            "refs": keep,
            "ref_candidates": [u for u in (args.get("ref_candidates") or []) if isinstance(u, str) and u.startswith("http")],
            "limitations": texts("limitations"),
            "next_questions": texts("next_questions"),
        }
        if tables:
            self.result['tables'] = tables
        if report:
            self.result['report'] = report
        if charts:
            self.result['charts'] = charts
        from .captures import retain
        captures = retain(getattr(self, 'session_documents', {}))
        if captures:
            self.result['captures'] = captures
        if getattr(self, 'lesson_draft', None):
            self.result['lesson'] = copy.deepcopy(self.lesson_draft)
        if getattr(self, "unavailable", None):  # a model it could not use, with why: a fact of the run, not its words
            self.result['unavailable'] = copy.deepcopy(self.unavailable)
        # A fact beside its sources (not a verdict on them): which cited pages none of its searches or fetches returned.
        # Only when its engine gives the pages' own addresses (a grounding redirect is not one).
        seen = {self._norm_url(u) for u in [*getattr(self, "search_sources", []),
                                            *[x for r in self.refs.values() for x in (r.get("url"), r.get("page")) if x]]}
        if any("vertexaisearch" not in u for u in seen):
            unseen = [u for u in self.result["sources"] if self._norm_url(u) not in seen]
            if unseen:
                self.result["sources_not_seen"] = unseen
        incomplete = [f"Its {r['part']} could not be kept: {r['error']}" for r in self.rejected]
        if getattr(self, "without_finish", False):
            incomplete.append("It answered in plain text without calling finish"
                              + ("" if getattr(self, "search_sources", None) else "; it searched nothing, so this is from the model's own knowledge"))
        if incomplete:
            # Never "done" over a lost part: the outcome is partial (run), and what was sent is kept beside it.
            self.result["incomplete"] = incomplete
            if self.rejected:
                self.result["rejected"] = copy.deepcopy(self.rejected)
        cited = claim_check.claims_of(args.get("claims"))
        if cited:
            self.result["claims"] = cited
        used = evidence_check.ids_of(args.get("evidence"))
        points = evidence_check.session_claims_of(args.get("session_claims"))
        if used or points:  # checked in the session's index before it is announced (check_evidence)
            self.result["evidence"] = list(dict.fromkeys(used + [i for p in points for i in p["evidence"]]))
            if points:
                self.result["session_claims"] = points
        return {"ok": True}, None

    async def check_claims(self) -> None:
        """Each point the result says came from a page is looked for in that page's text before
        the result is announced (claims.py); a scripted researcher reads its pages from the
        script ("pages": {url: text}) and fetches nothing. The points are kept word for word."""
        pages = (self.spec.get("fake") or {}).get("pages") if self.spec.get("fake") is not None else None
        if self.spec.get("fake") is not None and pages is None:
            return

        async def scripted(url: str) -> str:
            if url not in pages:
                raise FetchRefused("HTTP 404")
            return str(pages[url])

        judge = None
        if self.spec.get("claim_check") == "model" and pages is None:  # option CLAIM_CHECK (docs/bench/claims.md)
            async def judge(prompt: str) -> dict:
                return await fast_json(prompt, self)
        # A point checked already (save_findings, then the same point in finish) keeps its check: only new or changed
        # points are looked up again (their pages fetched once more otherwise).
        done = getattr(self, "_claims_checked", {})
        fresh = [c for c in self.result["claims"] if (c["point"], c["source"]) not in done]
        if fresh:
            for c in await claim_check.check(fresh, scripted if pages is not None else fetch_text, judge):
                done[(c["point"], c["source"])] = c
        self._claims_checked = done
        checked = [done[(c["point"], c["source"])] for c in self.result["claims"]]
        self.result["claims"] = checked
        found = sum(c["found"] is True for c in checked)
        missing = sum(c["found"] is False for c in checked)
        unread = sum(c["found"] is None for c in checked)
        self.step(f"Checked {len(checked)} sourced points against their pages: {found} found, {missing} not found in "
                  f"its sources" + (f", {unread} could not be checked" if unread else ""), "claims")

    def _saw(self, *entries: dict) -> None:
        """Every version of each entry it read (a population's revision): what its points may rest on."""
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id") and isinstance(entry.get("version"), int):
                versions = self.session_seen.setdefault(str(entry["id"]), [])
                if entry["version"] not in versions:
                    versions.append(entry["version"])

    async def check_evidence(self) -> None:
        """Each session entry the result cites is looked up in the session's index before the result is
        announced (evidence.py, ADR 0015): a point whose evidence is not there is marked, never left standing."""
        ids = list(self.result.get("evidence") or [])
        resolved = await asyncio.to_thread(evidence_check.resolve, ids, self.session_index, seen=self.session_seen)
        evidence_check.apply(self.result, resolved)
        found = sum(r.get("found") is True for r in resolved.values())
        gone = sum(r.get("found") is False for r in resolved.values())
        unread = sum(r.get("found") is None for r in resolved.values())
        cited = f"{len(ids)} {'entry' if len(ids) == 1 else 'entries'}"
        self.step(f"Checked the {cited} it cites in this Project: {found} found"
                  + (f", {gone} not in this Project" if gone else "")
                  + (f", {unread} could not be checked" if unread else ""), "claims")

    def tidy(self) -> None:
        """Delete reference pictures that were fetched but not cited. A partial result never
        got to cite any, so it keeps them all (listed as unreviewed in sources.json)."""
        partial = bool((self.result or {}).get("partial"))
        kept = {r["path"] for r in (self.result or {}).get("refs", [])}
        kept |= self.saved_finding_refs
        if partial:
            kept |= {r["path"] for r in self.refs.values()}
        for ref in self.refs.values():
            if ref["path"] not in kept:
                (self.dir / ref["path"]).unlink(missing_ok=True)
        (self.dir / "sources.json").write_text(json.dumps(
            [r | ({"unreviewed": True} if partial else {}) for r in self.refs.values() if r["path"] in kept], indent=2))

    # --- the run ----------------------------------------------------------------------
    async def run(self, engine) -> None:
        self.emit("status", status="running")
        self.started_at = time.monotonic()
        status = "done"

        async def alive() -> None:  # the app's watchdog stops only a researcher it stops hearing from
            while True:
                await asyncio.sleep(ALIVE_EVERY_S)
                self.emit("alive")

        ticker = asyncio.create_task(alive())
        try:
            # Ordinary research has a write-up hint. The explicitly advertised builder window is a hard bound.
            async with asyncio.timeout(self.effort.seconds if self.effort.name == "build" else None):
                if self.revising:
                    self.offline = self.spec.get("fake") is not None or bool(getattr(engine, "offline", False))
                    await self.revise()
                else:
                    await engine.run(self)
        except TimeoutError:
            status = "timeout"
            self.step("The builder reached its time limit; its draft and findings are kept", "warning")
        except asyncio.CancelledError:
            status = "cancelled"
        except OverBudget as exc:
            status = "over_budget"
            self.step(f"Over budget: {said(exc)}", "warning")
        except RunFailed as exc:  # its reason is the whole story
            status = "failed"
            self.step(said(exc)[:300], "warning")
        except Exception as exc:
            import traceback

            status = "failed"
            where = traceback.extract_tb(exc.__traceback__)[-1]
            self.step("Something went wrong; it keeps what it found", "warning",
                      detail=f"{type(exc).__name__}: {exc} (at {Path(where.filename).name}:{where.lineno} in {where.name})"[:600])
        finally:
            ticker.cancel()
        stopped = status in ("cancelled", "timeout", "over_budget") or self.effort.name == "build"
        if self.revising:
            # A new version or nothing: the work's result stays as it was (no write-up).
            status = "done" if self.made or self.lesson_draft else ("failed" if status == "done" else status)
            self.emit("done", status=status, result={'lesson':self.lesson_draft} if self.lesson_draft else None, spent=round(self.budget.spent, 4),
                      spent_estimated=round(self.spent_estimated, 4), response_id=None, engine=engine_name(engine))
            return
        if (self.result is None and status in ("done", "timeout", "over_budget", "failed", "cancelled")
                and (self.made or self.recorded or self.search_sources or self.ran_facts)):
            # It found, made or measured something but did not write up: keep the work, described from itself.
            self.result = partial_result(self.brief(), self.spec.get("label") or "", self.made, status,
                                         findings=self.recorded, searched=self.search_sources, operations=self.ran_facts)
            if self.rejected:
                self.result["rejected"] = copy.deepcopy(self.rejected)
            if getattr(self, "unavailable", None):
                self.result["unavailable"] = copy.deepcopy(self.unavailable)
            self.step("Kept what it found and made (no write-up)", "warning")
            status = "partial"
        if self.result is None and status == "done":
            status = "failed"
        if self.result is not None and self.result.get("incomplete") and status == "done":
            status = "partial"
        if (not stopped and self.result is not None and self.result.get("claims") and status in ("done", "partial")
                and self.spec.get("purpose", "research") == "research"):  # a label's description is not announced
            try:
                await self.check_claims()  # before the result is announced (W6-4c)
            except asyncio.CancelledError:
                status = "cancelled"
            except Exception as exc:  # noqa: BLE001 - an unchecked result is still a result
                self.step("Could not check its sources", "warning", detail=f"{type(exc).__name__}: {exc}"[:600])
        if (not stopped and self.result is not None and self.result.get("evidence") and status in ("done", "partial")
                and self.spec.get("purpose", "research") == "research"):
            try:
                await self.check_evidence()  # before the result is announced
            except asyncio.CancelledError:
                status = "cancelled"
            except Exception as exc:  # noqa: BLE001 - an unchecked result is still a result
                self.step("Could not check its session evidence", "warning", detail=f"{type(exc).__name__}: {exc}"[:600])
        if getattr(engine, "over_budget", False) and status == "done":
            status = "over_budget"
        self.tidy()
        self.emit("done", status=status, result=self.result, spent=round(self.budget.spent, 4),
                  spent_estimated=round(self.spent_estimated, 4), response_id=self.response_id,
                  engine=engine_name(engine))
