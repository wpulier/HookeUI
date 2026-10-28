"""Researchers: background subagents the brain starts, run in their own process.

The manager owns their sessions: it takes the snapshot they start from, queues them into
slots (3 big, 4 instant of which descriptions may use 2), refuses new ones past the daily
spending cap, writes session.json and the findings ledger, answers their requests for a
fresh snapshot, cancels any that overrun their time (+30 s), and tells the rest of the app
when one finishes (on_done). Starting one returns at once: the live conversation never
waits for a researcher (AGENTS.md "Live never waits on deep work").

Files: services/research/<date>/<rid>/ (git-ignored), served read-only under /research/
(files.py); services/research/findings.jsonl is the provenance ledger.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import itertools
import logging
import json
import re
import os
import time
import uuid
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import cv2
import numpy as np

from research_worker.budget import EFFORTS, WATCHDOG_SILENT_S
from research_worker.claims import summary_line as claims_line, unsupported as unsupported_claims
from research_worker.evidence import summary_line as evidence_line, unavailable as unavailable_evidence
from research_worker.results import partial_result
from research_worker.work import CITATION  # the search tool's citation markers

from .. import versions
from ..day import sid as sids
from ..learner import of as learner_of
from ..magnifier import crop_jpeg, object_box_in
from ..marks import encode_crop, fov_crop
from ..paths import Paths
from ..presenter import items as presenter_items
from .content import lesson_from_html, render
from . import findings as scientific_findings
from ..untrusted import block, inline, strip
from .ipc import ProcessChannel, WorkerGone
from .ledger import DayLedger
from .policy import CHOOSABLE, REVISE_EFFORT, ResearchRefused, admit
from .run import Delivery, Run, interrupted
from .missions import Mission, Missions, field_now, plan as mission_plan

log = logging.getLogger(__name__)
POOLS = {"big": 3, "instant": 4}
DESCRIPTION_SLOTS = 2
SNAPSHOT_EVERY_S = 10.0
TERMINAL_RECEIPT_GRACE_S = 10.0  # cancel acknowledgement is not proof the worker stopped


def strip_citations(value):
    """Researchers' text without the web search tool's inline citation markers."""
    if isinstance(value, str):
        return re.sub(r"\s+([.,;:])", r"\1", re.sub(r"  +", " ", CITATION.sub("", value))).strip()
    if isinstance(value, list):
        return [strip_citations(v) for v in value]
    if isinstance(value, dict):
        return {k: copy.deepcopy(v) if k == 'report' else strip_citations(v) for k, v in value.items()}
    return value


def engine_key_refusal(setting: str | None) -> ResearchRefused | None:
    """A researcher asked for while the model it would think with has no key: what the person is told (the model, the
    key it needs and where to add it), else None. The engine is the one the worker runs (research_worker/engines.py
    make_engine: its override, else the setting), and a key is there as the vision models decide it (agent.key_needed:
    the keys file and the Keychain are put in this process's environment, keys.set_environment, at the app's start and
    on every change, KeyAdmin.apply; the worker inherits them). Without this the worker's client failed at its first
    round and the card said only "could not finish: Something went wrong" (the 0.4.0 release candidate: a Google key
    alone, Gemini as the Assistant, GPT-6.1 Sol for researchers)."""
    from ..options import RESEARCH_ENGINE

    name = os.environ.get("MICROMIND_RESEARCH_ENGINE") or setting or "gpt-6.1-sol"
    option = RESEARCH_ENGINE.get(name)
    if option is None or not option.needs or os.environ.get(option.needs):
        return None  # a scripted engine, one this app does not list, or its key is there
    from gpu_worker.manifests import get, services

    from ..key_admin import _short

    service = next((s for s in services().values() if s.key == option.needs), None)
    entry = get(option.model) if option.model else None
    model = entry.name if entry is not None else option.label
    return ResearchRefused(f"{model}, the model researchers use, needs your "
                           f"{_short(service.name) if service else option.needs} key",
                           next="add it in Models › Keys, then try again")


ACTIVE = ("queued", "starting", "running")
MISSION_EFFORTS = ("quick", "standard")  # a mission's model runs: its hands need quick; deep's 18 min is no checkpoint


def mission_key(rid: str) -> str:
    """The day ledger's key for a mission's model runs still to come (never a researcher's id)."""
    return f"{rid}#mission"


def mission_line(mission: Mission) -> str:
    """Its mission in one line, for the brain (readable): the plan, where it stands and what the slots made so far."""
    hhmm = lambda at: time.strftime("%H:%M:%S" if mission.acquisition else "%H:%M", time.localtime(at))  # noqa: E731
    counts = ", ".join(f"{n} {k}" for k, n in mission.counts().items() if n) or "no slot yet"
    state = {"watching": f"watching, next slot due {hhmm(mission.next_due())}" if mission.next_due() else "watching",
             "checkpoint": "reading its slots (a checkpoint)", "finishing": "writing its finding",
             "done": "ended", "cancelled": f"stopped ({mission.why_ended})" if mission.why_ended else "stopped",
             "failed": f"failed ({mission.why_ended})",
             # Nothing carries an interrupted mission on yet (P2c-2): it is cancelled, never promised otherwise.
             "interrupted": "interrupted by the app stopping; cancel it to start a new one"}.get(mission.status,
                                                                                                         mission.status)
    made = [r.entry for r in mission.ledger if r.entry]
    entries = made[-5:]
    newest = (f"; newest entries: {', '.join(entries)}" + (f" (the last {len(entries)} of {len(made)})" if len(made) > len(entries) else "")
              if entries else "")
    return (f"Mission: {mission.operation} every {mission.every_s / 60:g} min for {mission.window_s / 60:g} min, "
            f"{len(mission.ledger)} of {mission.slots} slots ({counts}); {state}" + newest + ".")


def mission_words(mission: Mission, final: bool, next_at: float | None = None, newest: int = 12) -> str:
    """What a mission's model run is told: the ledger as the app's own facts (each slot's time, outcome and the entry
    it made, read with the session tool by id), the newest `newest` rows at a checkpoint, every row at the end."""
    hhmm = lambda at: time.strftime("%H:%M:%S" if mission.acquisition else "%H:%M", time.localtime(at))  # noqa: E731
    counts = ", ".join(f"{n} {k}" for k, n in mission.counts().items() if n)
    rows = mission.ledger if final else mission.ledger[-newest:]
    said = "; ".join(
        f"slot {r.n} (due {hhmm(r.due)}{', ran ' + hhmm(r.ran_at) if r.ran_at else ''}): {r.outcome}"
        + (f", {r.entry}" if r.entry else "")
        + (f" ({r.count}{' frames' if mission.acquisition else ''})" if r.count is not None else "")
        + (f" - {r.why}" if r.why else "") for r in rows)
    plan_ = (f"the app runs {mission.operation} every {mission.every_s / 60:g} min for {mission.window_s / 60:g} min "
             f"on the field pinned at {hhmm(mission.started_at)}")
    if mission.acquisition:
        plan_ += (f"; each slot requested {mission.acquisition['seconds']:g} fresh seconds. "
                  "The acq- entries contain original retained frames, not cell counts or completed analysis. "
                  "Read their actual duration and gaps. Use run with input=the retained id for motion or "
                  "segment with a frame number; do not acquire again to replace a missed or failed slot")
    if final:
        return (f"Your mission has ended ({mission.why_ended or 'every slot has a row'}): {plan_}; {counts}. The "
                f"ledger, every row: {said}. Read the entries you need with the session tool, by id, and write your "
                "finding: cite the entries it rests on (finish.evidence), and say what the missed or not comparable "
                "slots leave unresolved (finish.limitations).")
    return (f"Mission checkpoint: {plan_}; so far {counts}. The ledger's newest rows: {said}. Read what you need with "
            "the session tool, by id, say what has changed, and finish with what you can tell so far"
            + (f"; the mission goes on (next slot due {hhmm(next_at)})." if next_at else "."))
KEPT = ("done", "partial")
# What a new version can be made of (Researcher.revise): a photo without labels is refused with its own reason.
REVISABLE = ("lesson", "labelled picture", "illustration", "video", "picture")  # finished with work worth showing (partial: stopped before writing up, its outputs kept)

WORKING_MAX = 40  # records of the session a researcher looked at, kept for its card (the newest; the rest counted)
WORKING_SHOWN = 12  # of them, said to the brain per run (the newest)
ENTRY_ID = re.compile(r"[\w.:-]{1,80}")  # an index entry's id, as the research process checks it
LOOKED_HOW = re.compile(r"read(?: page)?|picture \d{1,3}")
# The kind an id's own form says, for a record whose title the app no longer has (a picture of a bookmark, a
# snapshot or a researcher's work): the index's forms (resources.Index.picture).
ID_KINDS = ((re.compile(r"e\d+"), "population"), (re.compile(r"d\d+"), "result"), (re.compile(r"b\d+"), "bookmark"),
            (re.compile(r"k\d+"), "clip"), (re.compile(r"snapshot-\d{6}-(?:[0-9a-f]{12}-)?f\d+"), "snapshot"),
            (re.compile(r"r\d{6,}"), "research"))
KIND_WORDS = {"population": "cell outlines", "clip": "a held clip", "result": "a result", "research": "a researcher's work",
              "snapshot": "a snapshot", "named": "a thing named", "taught": "a label taught", "scale": "the scale",
              "dataset": "a dataset", "recording": "a recording", "bookmark": "a bookmark", "track": "a track"}


@dataclass
class WorkerAttempt:
    """One queued/dispatching worker run, with its own completion rather than a mutable Session projection."""
    number: int
    origin: dict
    mission: Mission | None = None
    phase: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    completion: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future(), repr=False)
    forced_status: str | None = None  # watchdog exhaustion remains timeout even if the worker acknowledges cancel
    terminating_task: asyncio.Task | None = field(default=None, repr=False)
    record: dict = field(default_factory=dict)
    delivery: Delivery | None = field(default=None, repr=False, compare=False)

    def facts(self) -> dict:
        return {"runId": self.id, "run": self.number, "origin": copy.deepcopy(self.origin),
                **({"mission": True, "missionId": self.mission.id, "missionPhase": self.phase}
                   if self.mission is not None else {})}


def _whole(text: str, what: str) -> str:
    """What the user or a model wrote, kept whole (research_worker/limits.py), or refused saying why: never cut."""
    from research_worker.limits import TooLong, whole

    try:
        return whole(text, what)
    except TooLong as exc:
        raise ResearchRefused(str(exc), next="say it in fewer words, or split it into two requests") from None


@dataclass
class Session:
    rid: str
    n: int
    brief: str
    deliverable: str | None
    effort: str
    engine: str
    purpose: str  # "research" | "description"
    label: str
    object_id: str | None
    origin: dict  # {"channel", "delegation_id", "turn_id", "request_id"?}
    day: str
    deliver: str | None = None  # "tab": new workspace research, kept in Data and Researchers, never placed in a saved Tab by the app
    status: str = "queued"
    step: str = ""
    step_at: float | None = None
    spent: float = 0.0
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    outputs: list[dict] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    corrections: list[dict] = field(default_factory=list)
    result: dict | None = None
    finding_revisions: list[dict] = field(default_factory=list)
    response_id: str | None = None
    fake: dict | None = None
    history: list[dict] = field(default_factory=list)  # earlier questions and answers (follow-ups)
    last_snapshot: float = 0.0
    has_crop: bool = False
    restored: bool = False  # loaded from disk after a restart
    # The session it was started in (a session id, day/sid.py; ADR 0017): it belongs there whatever session is open, and
    # is continued only there. `day` is the calendar date folder its files are in (research/<day>/<rid>).
    sitting: str = ""
    specimen: str = ""  # what was in view when it started
    spent_estimated: float = 0.0  # calls cancelled by a stop: billed, usage unknown (estimated)
    run: Run | None = None  # a follow-up answer or a new version running on its finished work (run.py)
    last: dict | None = None  # the history entry of the run that just ended, for on_done hooks (the announcer)
    notes: list[dict] = field(default_factory=list)  # the user's notes while it works ({"at", "text"}; inbox.jsonl)
    plan: dict | None = None  # what a deep run said it would look up first ({"searches", "sources"}; W6-4d)
    # Evidence it asked the user for ({"id", "what", "why", "seconds", "at", "status": asked | answered,
    # "answer"}); the user's next note answers the oldest one still asked (request_evidence).
    requests: list[dict] = field(default_factory=list)
    # What it looked at in the session's index ({"id", "how", "title", "kind", "version", "run", "at"}; the mission
    # card's working set), once per record, way, revision and run: the index's own titles, the app's words. The
    # newest WORKING_MAX are kept; `working_dropped` counts the older ones let go.
    working: list[dict] = field(default_factory=list)
    working_dropped: int = 0
    # Scientific provenance outlives the bounded card display. Metadata only.
    evidence_reads: list[dict] = field(default_factory=list)
    evidence_reads_missing: int = 0
    # The app's operations it ran (hands.py: id, operation, why, run, at, count, title, kind): what it had the app
    # make, the app's own record (never the research process's echo).
    ran: list[dict] = field(default_factory=list)
    # Its mission (missions.Mission: the plan, the pinned field and the ledger) while it has one (P2c), and the
    # ones before it, each with its ledger (never replaced).
    mission: Any = None
    past_missions: list = field(default_factory=list)
    result_run: dict | None = None  # producing run's facts for the current finding; kept when it moves to history
    interaction_jobs: list[dict] = field(default_factory=list)
    presentation_jobs: list[dict] = field(default_factory=list)
    usage_runs: dict[str, dict] = field(default_factory=dict)
    dependencies: list[dict] = field(default_factory=list)
    admission: dict | None = None  # validated original intent/quote, bound to the selected effort and deliverable
    asking: str = ""  # what its newest run (a continue) was asked; `brief` stays the original request
    # Questions asked while it was working: answered from its work as soon as its current run ends, never refused.
    pending: list[dict] = field(default_factory=list)
    # What a continue's worker is given (ResearchManager._continuation): built when it is admitted, never saved.
    package: str = field(default="", repr=False, compare=False)
    # A population's revision now ((session, id) -> revision or None), so what it read is said "corrected since" when
    # it has changed (the partner's Amendment 1, C1); set by the manager.
    revision_now: Callable[[str, str], int | None] | None = field(default=None, repr=False, compare=False)
    root: Path | None = field(default=None, repr=False, compare=False)  # the research directory
    _lessons: dict = field(default_factory=dict, repr=False, compare=False)  # legacy lessons read from their HTML, by path
    _before: dict = field(default_factory=dict, repr=False, compare=False)  # spending and outputs before this run
    _notes_from: int = field(default=0, repr=False, compare=False)  # notes before this run was queued (read already)
    _attempt: WorkerAttempt | None = field(default=None, repr=False, compare=False)
    _delivery: Delivery | None = field(default=None, repr=False, compare=False)
    _pending_delivery: Delivery | None = field(default=None, repr=False, compare=False)  # who asked a kept question
    _heard: float = field(default=0.0, repr=False, compare=False)  # when its research process last said anything
    _completion: dict | None = field(default=None, repr=False, compare=False)
    renumbered: bool = field(default=False, repr=False, compare=False)  # given a new number when read (read_day)

    def __post_init__(self) -> None:
        if not self.sitting:  # one made without a session sits in the session of its date (day/sid.py)
            self.sitting = self.day

    @property
    def base(self) -> str:
        return f"/research/{self.day}/{self.rid}"

    @property
    def answered(self) -> dict | None:
        """The follow-up answer the run that just ended gave (its history entry), or None."""
        return self.last if self.last and self.last.get("mode") == "answer" else None

    @property
    def revised(self) -> dict | None:
        """What the revise that just ended made: {"id", "version", "kind", "change", "status"}, or None."""
        last = self.last
        if not last or last.get("mode") != "revise":
            return None
        return {"id": last.get("output"), "version": last.get("version"), "kind": last.get("kind"),
                "change": last.get("change"), "status": last.get("status")}

    @property
    def name(self) -> str:
        return f"Researcher {self.n}" if self.n else "Quick lookup"

    def completed_view(self) -> Session:
        """Detach the just-completed run before any listener can await or continue it.

        Keep the Session reading interface for existing listeners, without copying
        live voice objects, futures, tasks or revision callbacks into persisted facts.
        """
        view = copy.copy(self)
        for key in ("origin", "outputs", "findings", "finding_revisions", "interaction_jobs", "presentation_jobs", "usage_runs", "dependencies", "corrections", "result", "history", "last", "notes",
                    "plan", "requests", "working", "evidence_reads", "ran", "result_run", "admission", "_lessons", "_before"):
            setattr(view, key, copy.deepcopy(getattr(self, key)))
        attempt = self._attempt
        if attempt is not None:
            view.origin = copy.deepcopy(attempt.origin)
            view._delivery = attempt.delivery
            if attempt.completion.done():
                view._completion = copy.deepcopy(attempt.completion.result())
        return view

    def run_number(self) -> int:
        """The number of the run going now, one numbering for every reader (its attempt, what it looked at, its
        questions): a continue files the run before it as it starts; an answer or a new version files its own as it
        ends, so it is the next number while it runs."""
        return len(self.history) + (1 if self.run is not None else 0)

    @property
    def run_effort(self) -> str:
        """The effort of what it is doing now (a run's own, else its work's)."""
        return self.run.effort if self.run is not None else self.effort

    def wire(self) -> dict:
        result = dict(self.result or {})
        if result.get("refs"):
            result["refs"] = [{**r, "url": f"{self.base}/{r['path']}"} for r in result["refs"]]
        gone = versions.replaced_ids(self.outputs)
        revising = self.run is not None and self.run.kind == "revise" and self.status in ("starting", "running")
        return {
            "rid": self.rid, "day": self.day, "n": self.n, "name": self.name, "brief": self.brief, "deliverable": self.deliverable,
            "effort": self.effort, "engine": self.engine, "purpose": self.purpose, "label": self.label,
            **({"deliver": self.deliver} if self.deliver is not None else {}),
            "objectId": self.object_id, "status": "revising" if revising else self.status, "step": self.step,
            **({"stepAt": self.step_at} if self.step_at is not None else {}),
            "spent": round(self.spent, 4), "spentEstimated": round(self.spent_estimated, 4),
            **(self.run.wire() if self.run is not None else {"answering": None, "revising": None}),
            "notes": len(self.notes), "thread": (self.origin or {}).get("thread"),
            # The request that started (or last continued) it, for the chat's cards (match on requestId), and its
            # typed turn: turnId is null without one (voice and manual starts have no user turn, recorded as 0).
            "turnId": (self.origin or {}).get("turn_id") or None, "requestId": (self.origin or {}).get("request_id"),
            **({"plan": self.plan} if self.plan else {}),
            **({"requests": self.requests} if self.requests else {}),
            **({"working": [self._with_now(w) for w in self.working]} if self.working else {}),
            **({"workingDropped": self.working_dropped} if self.working_dropped else {}),
            **({"ran": [self._with_now(r) for r in self.ran]} if self.ran else {}),
            **({"mission": self.mission.wire()} if self.mission is not None else {}),
            **({"pastMissions": [m.wire() for m in self.past_missions]} if self.past_missions else {}),
            **({"resultRun": copy.deepcopy(self.result_run)} if self.result_run else {}),
            # The run going now: its cap and its time from the same effort (an answer or a new version has its own).
            "cap": EFFORTS[self.run_effort].cap_usd, "seconds": EFFORTS[self.run_effort].seconds,
            "created": self.created, "started": self.started, "finished": self.finished,
            "snapshot": f"{self.base}/inputs/crop.jpg" if self.has_crop else f"{self.base}/inputs/frame.jpg",
            "outputs": [{**o, "url": f"{self.base}/{o['path']}", "current": not o.get("conflict") and not (o.get("id") and o["id"] in gone)}
                        for o in self.outputs],
            # The result viewer's tabs, in the order and with the labels the presenter counts.
            # Each with what kind of thing it is (media.py), and a reference photo with which of result.refs it is.
            "items": [{"kind": it["kind"], "label": it["label"], "id": it.get("id"), "media": it.get("media"),
                       "line": it.get("line"), "work": it.get("work"),
                       **({"ref": it["ref"]} if "ref" in it else {})} for it in presenter_items(self)],
            "findings": self.findings[-20:], "findingCount": len(self.findings), "corrections": self.corrections,
            "result": result or None,
            "findingRevisions": [{k: row[k] for k in ('version', 'digest', 'at', 'complete')} for row in self.finding_revisions],
            "interactionJobs": copy.deepcopy(self.interaction_jobs),
            "presentationJobs": copy.deepcopy(self.presentation_jobs),
            "dependencies": copy.deepcopy(self.dependencies),
            "history": self.history, "canFollowUp": self.status not in ACTIVE and self.purpose == "research",
            "restored": self.restored, "sitting": self.sitting, "specimen": self.specimen,
        }

    def line(self) -> str:
        """One line for the brain's context. Its words are the researcher's (untrusted): each
        is one capped line without quotes or angle brackets, so the line is safe in any
        template; prompts still wrap the lines (untrusted.block)."""
        what = inline(self.label or self.brief, 60)
        conversation = inline((self.origin or {}).get("thread") or "unknown", 60)
        day = inline(self.day or "unknown", 20)
        attempt = self._attempt
        original = ("unavailable" if attempt is None or attempt.completion.cancelled() else
                    f"retained {attempt.number}, {'settled' if attempt.completion.done() else 'pending'}")
        # Retained work remains readable; these are origin/run facts, not a
        # recommendation to reuse it or a promise that its task was fulfilled.
        basis = (f" [origin conversation={conversation}, day={day}; "
                 f"{'restored' if self.restored else 'not restored'}; original run {original}]")
        if self.status in ACTIVE and self.run is not None:
            return f"{self.name} ({what}): {self.run.line()}" + basis
        if self.status in ACTIVE and self.asking:
            what += f", now on: {inline(self.asking, 80)}"
        if self.status in ACTIVE:
            open_ = next((r for r in self.requests if r.get("status") == "asked"), None)
            asking = f", asking the user for {inline(open_.get('what'), 100)} ({open_['id']})" if open_ else ""
            return f'{self.name} ({what}): {self.status}{", " + inline(self.step, 160) if self.step else ""}{asking}' + basis
        made = ", ".join(f"{o['id']} {versions.kind_name(o)}" for o in self.current_outputs() if o.get("id"))
        if made:
            basis = f" [made: {made}]" + basis
        if self.result:
            missing = unsupported_claims(self.result)
            gone = unavailable_evidence(self.result)
            return (f'{self.name} ({what}): {self.status}: {inline(self.result.get("one_liner") or self.result.get("summary", ""), 120)}'
                    + (f" ({len(missing)} of its points not found in its sources)" if missing else "")
                    + (f" ({len(gone)} of its points rest on evidence not found in the session)" if gone else "") + basis)
        return f"{self.name} ({what}): {self.status}" + basis

    # --- versions of what it made (orchestrator/versions.py) ------------------------------
    def current_outputs(self) -> list[dict]:
        """The latest version of each thing it made (an output and everything that replaces
        it, transitively, is one line), in the order they were made."""
        return versions.current(self.outputs)

    def output(self, oid: str | None) -> dict | None:
        return versions.find(self.outputs, oid)

    def versions_of(self, oid: str | None) -> list[dict]:
        """All versions of the thing `oid` is a version of, oldest first."""
        return versions.line(self.outputs, oid)

    def content_of(self, output: dict | None) -> dict | None:
        """An output's readable content; a lesson made before content was saved is read from
        its HTML (as text, never run)."""
        if not output:
            return None
        content = output.get("content")
        if isinstance(content, dict) and content:
            return content
        if output.get("kind") != "html" or not output.get("path") or self.root is None:
            return None
        if output["path"] not in self._lessons:
            directory = (self.root / self.day / self.rid).resolve()
            path = (directory / output["path"]).resolve()
            try:
                text = path.read_text(errors="ignore")[:3_000_000] if path.is_relative_to(directory) else ""
            except OSError:
                text = ""
            self._lessons[output["path"]] = lesson_from_html(text) or {}
        return self._lessons[output["path"]] or None

    # --- its work, readable (for the brain's read_result / result_step tools) -----------
    def lesson(self) -> dict | None:
        """The lesson it made ({"kind": "lesson", "title", "key_ideas", "steps"}), its current
        version; when it made more than one (a "continue"), the newest."""
        for output in reversed(self.current_outputs()):
            content = output.get("content")
            if isinstance(content, dict) and content.get("kind") == "lesson":
                return content
            if output.get("kind") == "html" and not content:
                found = self.content_of(output)
                if found:
                    return found
        return None

    def steps(self) -> list[dict]:
        """The lesson's steps, in order: [{"id": "q1", "prompt", "answer", "hint"}] (empty when
        it made no lesson)."""
        return list((self.lesson() or {}).get("steps") or [])

    def _with_now(self, w: dict) -> dict:
        """A working record, with the revision of what it read now when that has changed since (C1)."""
        base = {k: v for k, v in w.items() if k != "now"}  # a saved "now" is from then: worked out again
        now = self.revision_now(self.sitting, w["id"]) if self.revision_now is not None and w.get("version") is not None else None
        return {**base, "now": now} if now is not None and now != w.get("version") else base

    def working_lines(self, newest: int = WORKING_SHOWN) -> list[str]:
        """What it had the app make (`ran`, the app's own record) and what it looked at in the session's index
        (`working`), each run apart (its result, then each follow-up), the newest `newest` of each; a record read
        at a revision corrected since says so (C1). The app's words: titles from the index, ids one step away."""
        from ..untrusted import inline

        one = lambda x: " ".join(str(x or "").split())  # noqa: E731
        runs = {int(x.get("run") or 0) for x in [*self.ran, *self.working]}
        # Run n's question: an answer's entry (filed as it ends) holds its own question, a revision's its change; a
        # continue's entry (filed as it starts, with the run before it) holds the question it answers as `next`.
        asked = [(h.get("brief") if h.get("mode") == "answer" else h.get("change") if h.get("mode") == "revise"
                  else h.get("next")) for h in self.history] + (
            [self.run.question or self.run.change] if self.run is not None else [])
        continued = any(not h.get("mode") for h in self.history)  # its result is now a later run's

        def where(run: int) -> str:
            """Which run: its result (its first, once a continue made a newer one), or a later run by the question
            it answered (a revision by its change; an older saved continue without its question, by number)."""
            if runs <= {0}:
                return ""
            if run == 0:
                return " for its first result" if continued else " for its result"
            question = inline(asked[run - 1]) if run - 1 < len(asked) and asked[run - 1] else ""
            cut = question[:80] + ("…" if len(question) > 80 else "")  # one line, its own quotes out (re-check note)
            return f' for the later question "{cut}"' if question else f" for follow-up {run}"

        def say(items: list[dict], verb: str = "read") -> str:
            groups: dict[str, dict] = {}
            for w in sorted(items, key=lambda w: w.get("at") or 0):
                g = groups.setdefault(w["id"], {"title": "", "kind": "", "pictures": 0, "read": [], "now": None, "at": 0})
                g["title"] = g["title"] or one(w.get("title"))
                g["kind"] = g["kind"] or one(w.get("kind"))
                g["pictures"] += str(w.get("how") or "").startswith("picture")
                if w.get("version") is not None and w["version"] not in g["read"]:
                    g["read"].append(w["version"])
                g["now"] = self._with_now(w).get("now", g["now"])
                g["at"] = w.get("at") or 0
            listed = sorted(groups.items(), key=lambda kv: kv[1]["at"])
            shown = listed[-newest:]
            words = []
            for eid, g in shown:
                corrected = (f", {verb} at revision {', '.join(map(str, g['read']))}, corrected since (now {g['now']})"
                             if g["now"] is not None and g["read"] and g["now"] not in g["read"][-1:] else "")
                pictures = f", {g['pictures']} picture{'s' if g['pictures'] > 1 else ''}" if g["pictures"] else ""
                words.append(f"{g['title'] or KIND_WORDS.get(g['kind'], 'a record')} ({eid}{corrected}){pictures}")
            more = len(listed) - len(shown)
            return "; ".join(words) + (f"; and {more} more" if more else "")

        lines = []
        for run in sorted(runs):
            made = [{**x, "how": "made"} for x in self.ran if int(x.get("run") or 0) == run and x.get("id")]
            looked = [w for w in self.working if int(w.get("run") or 0) == run]
            if made:
                lines.append(f"It had the app make{where(run)}: {say(made, 'made')}.")
            if looked:
                lines.append(f"It looked at{where(run)}: {say(looked)}.")
        if self.working_dropped:
            lines.append(f"({self.working_dropped} earlier records it looked at are not listed.)")
        return lines

    def readable(self, part: str | None = None, limit: int | None = 2500) -> str:
        """Its work as plain text: what it was asked, its result, what it made (a lesson's
        questions with answers, a picture's labels and where they point, an illustration's
        prompt), its sources and earlier follow-ups. `part` narrows it: "summary", "q2" (or
        "2"), "labels", "sources", "history" or "lesson". Researchers' text comes from the web:
        callers keep it marked untrusted."""
        r = self.result or {}
        head = f"{self.name} ({_made(self)}{': ' + self.label if self.label else ''}), {self.status.replace('_', ' ')}"
        continued = self.history[-1] if self.history and not self.history[-1].get("mode") else None
        if not r and self.status in ACTIVE and self.asking and continued and continued.get("result"):
            # A continue is working: the finding it continues is still its finding, said to be the one before this run.
            r = continued["result"]
            version = (continued.get("version") or {}).get("version")
            head += (f"\nIts finding{f' (version {version})' if version else ''}, from before this run; a new run is "
                     f"working on: {' '.join(str(self.asking).split())}")
        elif not r:
            head += "\nNo written result." if self.status not in ACTIVE else "\nStill working; no written result yet."
        # A researcher's words are one line each, whatever they hold, so they can never forge the app's
        # own lines below ("Sources checked: ...", "(It stopped before writing up ...)").
        one = lambda x: " ".join(str(x or "").split())  # noqa: E731
        # A report keeps its paragraphs, each later line indented: whole, and still never at the start of a line,
        # where the app's own lines are.
        indented = lambda x: "\n  ".join(line.strip() for line in str(x or "").strip().splitlines())  # noqa: E731
        summary = [f"Title: {one(r['title'])}"] if r.get("title") else []
        if r.get("summary"):
            summary.append(f"Summary: {one(r['summary'])}")
        if r.get("one_liner") and r.get("one_liner") != r.get("summary"):
            summary.append(f"In short: {one(r['one_liner'])}")
        summary += [f"- {one(d)}" for d in r.get("details") or []]
        tables = []
        for table in r.get('tables') or []:
            tables += [f"Table: {one(table['title'])}", ' | '.join(one(x) for x in table['columns'])]
            tables += [' | '.join(one(x) for x in row) for row in table['rows']]
        if r.get("look_for"):
            summary.append(f"Look for: {one(r['look_for'])}")
        qualifier_start = len(summary)
        summary += [f"- it cannot settle: {one(x)}" for x in r.get("limitations") or []]
        if r.get("next_questions"):
            summary.append("It suggests asking next: " + " | ".join(one(x) for x in r["next_questions"]))
        if r.get("partial"):
            summary.append("(It stopped before writing up; this is described from what it found and made.)")
        summary += [f"- incomplete: {one(x)}" for x in r.get("incomplete") or []]
        # What it recorded as it worked, whole: a partial result's own, or (no result at all) the run's records.
        recorded = r.get("findings") if r.get("partial") else ([] if r else self.findings)
        if recorded:
            summary.append("What it recorded as it worked:")
            summary += [f"- {one(f.get('text'))}" + (f" (from {', '.join(one(u) for u in f['sources'])})" if f.get("sources") else "")
                        for f in recorded if isinstance(f, dict)]
        checked = claims_line(r)
        if checked:
            # Checked by the app after it finished (research_worker/claims.py): its key terms on the page, or not.
            summary.append(f"Sources checked: {checked}.")
            summary += [f"- not found in its sources: {c['point']} ({c['source']})" for c in unsupported_claims(r)]
            summary += [f"- could not be checked ({c.get('why') or 'page not read'}): {c['point']}"
                        for c in r.get("claims") or [] if isinstance(c, dict) and c.get("found") is None]
        # The session's records it cites, looked up in the session's index before it was announced
        # (research_worker/evidence.py, ADR 0015): the ones not found are said, never left standing.
        def entry(e: dict) -> str:
            if e.get("found") is True:
                when = f", {one(e['time'])}" if e.get("time") else ""
                return f"{one(e['id'])} ({one(e.get('title') or e.get('kind') or 'an entry')}{when})"
            state = "not found in the session's index" if e.get("found") is False else f"not checked: {one(e.get('why'))}"
            return f"{one(e['id'])} ({state})"

        cited = [e for e in r.get("evidence") or [] if isinstance(e, dict)]
        if cited:
            summary.append("Evidence it used from the session: " + "; ".join(entry(e) for e in cited) + ".")
        for c in r.get("session_claims") or []:
            state = {True: "from the session", False: "evidence unavailable", None: "evidence not checked"}[c.get("found")]
            if c.get("found") is True and c.get("corrected"):  # it read an earlier revision of what it rests on
                state = "from the session as it was then, corrected since (" + "; ".join(
                    f"{one(x['id'])} read at {', '.join(map(str, x['read'] if isinstance(x['read'], list) else [x['read']]))}, "
                    f"now {x['now']}" for x in c["corrected"]) + ")"
            summary.append(f"- {state}: {one(c['point'])} ({', '.join(c.get('evidence') or [])})")
        if evidence_line(r):
            summary.append(f"The session's index: {evidence_line(r)}.")
        table_qualifiers = summary[qualifier_start:]
        # Tables can exceed a model reader's allowance. Their source qualifications
        # must precede the rows in every excerpt, just as they do in a focused read.
        summary += tables
        if r.get('report'):
            summary += [f"Full findings — {one(key)}: {indented(value)}" for key, value in r['report'].items()]
        # After the app's own qualifiers above, so a cut never parts a point from what was found about it.
        summary += self.working_lines()
        if self.mission is not None:
            summary.append(mission_line(self.mission))
        made = []
        lesson = self.lesson()
        # Numbered as show_result counts them (presenter.items: current versions, lessons,
        # videos, pictures; then older versions).
        for n, (output, now) in enumerate(versions.ordered(self.outputs), 1):
            kind = {"html": "lesson", "image": "picture", "video": "video"}.get(output.get("kind"), output.get("kind"))
            version = int(output.get("version") or 1)
            oid = f" {output['id']}" if output.get("id") else ""
            if not now:
                made.append(f"{n}. {kind}{oid}, version {version} (an older version, replaced): {output.get('caption') or output.get('path')}")
                continue
            if version > 1:
                kind += f", version {version}" + (f" (changed: {output['change']})" if output.get("change") else "")
            made.append(f"{n}. {kind}{oid}: {output.get('caption') or output.get('path')}")
            content = self.content_of(output) or (lesson if output.get("kind") == "html" else None)
            made += ["   " + line for line in render(content, compact=True)]
        unseen = set(r.get("sources_not_seen") or [])
        sources = [f"{u} (not returned by its searches or fetches)" if u in unseen else u for u in r.get("sources") or []]
        sources += [f"{x.get('title') or 'reference picture'} ({x.get('credit') or x.get('page') or ''})" for x in r.get("refs") or []]
        history = []
        for h in self.history:
            if h.get("mode") == "revise":
                history.append(f"Revised its {h.get('kind') or 'work'}"
                               + (f" (version {h['version']})" if h.get("version") else f" ({h.get('status')})")
                               + f": {h.get('change')}" + (f" (stopped: {one(h['why'])})" if h.get("why") else ""))
                continue
            answer = (h.get("result") or {}).get("summary") or (h.get("result") or {}).get("one_liner") or f"({h.get('status')})"
            history.append(f"{'Follow-up' if h.get('mode') == 'answer' else 'Asked earlier'}: {h.get('brief')}\n  Answer: {answer}")

        wanted = (part or "").strip().lower()
        step = re.fullmatch(r"(?:q|question|step)?\s*#?(\d{1,2})", wanted)
        if step:
            n = int(step.group(1))
            steps = self.steps()
            found = next((s for s in steps if s["id"] == f"q{n}"), None)
            if found is None:
                text = (f"{head}: no step q{n}; its lesson has {', '.join(s['id'] for s in steps)}" if steps
                        else f"{head}: it made no lesson with steps")
            else:
                index = steps.index(found) + 1
                page = next((o for o in reversed(self.current_outputs()) if o.get("kind") == "html"), {})
                version = (f" (version {page['version']}: {page.get('change') or 'revised'})"
                           if int(page.get("version") or 1) > 1 else "")
                text = "\n".join([f'{head}. Lesson "{(lesson or {}).get("title")}"{version}, {found["id"]} (question {index} of {len(steps)}):',
                                  found["prompt"]] + ([f"answer: {found['answer']}"] if found.get("answer") else [])
                                 + ([f"hint: {found['hint']}"] if found.get("hint") else []))
        elif wanted in ("table", "tables"):
            text = "\n".join([head] + table_qualifiers + (tables or ["No comparison tables."]))
        elif wanted in ("report", "findings", "full"):
            report = [f"Full findings — {one(key)}: {indented(value)}" for key, value in (r.get("report") or {}).items()]
            text = "\n".join([head] + table_qualifiers + (report or ["It wrote no full report."]))
        elif wanted in ("summary", "result"):
            text = "\n".join([head] + summary)
        elif wanted in ("labels", "label"):
            labels = [line for o in self.current_outputs() for line in render(o.get("content"))
                      if (o.get("content") or {}).get("kind") == "labels"]
            text = "\n".join([head] + (labels or ["It made no labelled picture."]))
        elif wanted in ("sources", "source"):
            text = "\n".join([head, f"Selected research engine: {one(self.engine)}", "Sources:"]
                             + ([f"- {s}" for s in sources] or ["(none cited)"]) + self.working_lines())
        elif wanted in ("history", "followups", "follow-ups", "answers"):
            text = "\n".join([head] + (history or ["No follow-up questions yet."]))
        elif wanted in ("lesson", "steps", "quiz"):
            text = "\n".join([head] + (render(lesson) or ["It made no lesson."]))
        else:
            text = "\n".join(
                ([f"(no part {part!r}: ask for summary, report, tables, q1, q2..., lesson, labels, sources or history)"] if wanted else [])
                + [head, f"Asked: {self.brief}"] + ([f"Now asked: {self.asking}"] if self.asking else []) + summary + (["Sources: " + "; ".join(sources)] if sources else [])
                + (["Earlier questions:"] + history if history else []) + (["Made:"] + made if made else []))
        text = strip(text)  # the researcher's words: nothing in them can open or close a tag
        if limit is None or len(text) <= limit:
            return text
        more = " … (cut here; ask for a part: summary, report, tables, q1, q2..., lesson, labels, sources or history)"
        return text[: limit - len(more)] + more if limit > 2 * len(more) else text[: limit - 1] + "…"


INBOX = "inbox.jsonl"  # the user's notes to a working researcher, one per line ({"at", "text"})


def _read_inbox(directory: Path) -> list[dict]:
    try:
        lines = (directory / INBOX).read_text().splitlines()
    except OSError:
        return []
    notes = []
    for line in lines:
        try:
            note = json.loads(line)
        except ValueError:
            log.warning("research inbox %s: a line that is not JSON was skipped", directory)
            continue
        if isinstance(note, dict) and note.get("text"):
            # Its marks ride with it (which question it answers, footage held for it), as the worker reads them.
            notes.append({"at": note.get("at"), "text": str(note["text"]),
                          **{k: note[k] for k in ("answers", "holding", "held") if isinstance(note.get(k), str)}})
        else:
            log.warning("research inbox %s: a line that is not a note was skipped", directory)
    return notes


def _working_ok(w) -> bool:
    """A saved working record the readers can use (a hand-edited or older file keeps none that would break them)."""
    return (isinstance(w, dict) and isinstance(w.get("id"), str) and bool(ENTRY_ID.fullmatch(w["id"]))
            and isinstance(w.get("how"), str) and isinstance(w.get("at"), (int, float)))


def _ran_ok(r) -> bool:
    """A saved record of a run the readers can use: its entry's id, and its run as a number when it says one."""
    return (isinstance(r, dict) and isinstance(r.get("id"), str) and bool(ENTRY_ID.fullmatch(r["id"]))
            and isinstance(r.get("run", 0), int) and not isinstance(r.get("run", 0), bool)
            and isinstance(r.get("at", 0), (int, float)) and not isinstance(r.get("at", 0), bool))


def _kept_working(d: dict) -> tuple[list[dict], int]:
    """A saved researcher's working set as read back: the usable records, the newest WORKING_MAX, and how many
    were let go (those counted before, and any more on disk)."""
    usable = [w for w in d.get("working") or [] if _working_ok(w)]
    dropped = int(d.get("workingDropped") or 0) + max(0, len(usable) - WORKING_MAX)
    return usable[-WORKING_MAX:], dropped


def _made(s: Session) -> str:
    return versions.made(s.outputs, None, nothing="research")  # one word, as the desk says it (versions.made)


def _sitting_of(saved: dict, folder: str) -> str:
    """The session a saved researcher was started in: its own "session", else (saved before sessions) the session of
    its folder's date (day/sid.py)."""
    return saved["session"] if sids.is_sid(saved.get("session")) else sids.session_of({}, folder)


def _of_session(record, sitting: str) -> bool:
    """Whether a store's record (a population, a held clip) was made in session `sitting`: by its own `session`, else
    (a stand-in without one) by the session of its date (day/sid.py); one that says neither is taken as it was
    before sessions."""
    made_in = getattr(record, "session", "") or getattr(record, "day", None)
    return made_in is None or made_in == sitting


def _month_capped() -> bool:
    """Whether the optional monthly cap on cloud models is set (research_worker.api_usage)."""
    from research_worker import api_usage

    return api_usage._active is not None and api_usage._active.cap() is not None


class ResearchManager:
    def __init__(self, hub, conversation=None, root: Path | None = None, worker_target: Callable | None = None,
                 today: Callable[[], str] | None = None, session: Callable[[], str] | None = None, store=None) -> None:
        self.hub = hub
        # The calendar day: the session store's date, which follows the clock (day/store.py). A new researcher's files
        # go into its folder (research/<day>/<rid>) and a run is charged to the day it starts on (ledger.py). The
        # clock's without one (tests).
        self.today: Callable[[], str] = today or (lambda: time.strftime("%Y-%m-%d"))
        # The session open now (ADR 0017: a session is the project, not the day), and its store: a researcher belongs
        # to the session it was started in (`Session.sitting`), is numbered in it and continued only in it. The store
        # says which dates a session touched (its researchers are filed in those dates' folders) and what it is called.
        # Without a store (tests, benches) the session is today's date: one session a day.
        self.store = store
        self.session: Callable[[], str] = session or ((lambda: store.sid) if store is not None else self.today)
        self.conversation = conversation
        self.root = Path(root) if root is not None else Paths.from_env().research
        # Where researchers read the session's one index (scaffolding Amendment 2, point 7): the app's own
        # address, set by server.main once it knows its port. Unset (tests, create_app alone): no session tool.
        self.resources_url: str | None = None
        # The researchers in memory: the open session's (every date it touched), every one still working of any
        # session, and those of sessions opened earlier in this run (kept, so a late receipt always finds its
        # researcher). What the brain is told, the shelf and the list are the open session's (`researchers`).
        self.sessions: dict[str, Session] = {}
        self.queue: list[str] = []
        self.on_done: list[Callable[[Session], None]] = []
        self.on_presentation: list[Callable[[Session, dict], None]] = []
        self.on_mission_failed: list[Callable[[Session, Mission, dict], None]] = []
        self._notified_mission_failures: set[tuple[str, str]] = set()
        self.on_change: list[Callable[[Session], None]] = []
        self.on_correction: list[Callable[[Session, dict], None]] = []
        self.on_plan: list[Callable[[Session], None]] = []  # a deep run said what it will look up first
        self.on_request: list[Callable[[Session, dict], None]] = []  # a researcher asked the user for evidence
        # The interaction log (agent.log; the app sets it): lesson checks, stalls and claims checks
        # are logged with the options used, for comparing them.
        self.log: Callable[[dict], None] = lambda record: None
        # Where each run's model transcript goes (a performance log, logs/transcripts/; the app sets it): one file per
        # researcher and day, a line per round (research_worker/researcher.py `transcribe`). Unset (tests): none kept.
        self.transcripts: Path | None = None
        # Its hands (hands.py): the app's own operations a researcher asks for, for its reading, never on screen.
        from .hands import Hands

        self.hands = Hands(hub, log=lambda record: self.log(record))
        # Missions (missions.py; the partner's Amendment 1 B): the app runs a researcher's planned operation on a
        # schedule through its hands, one ledger row per slot, saved with the researcher as it happens.
        self.missions = Missions(self._mission_slot, self._mission_field, self._mission_saved,
                                 checkpoint=self._mission_checkpoint, finish=self._mission_finish,
                                 log=lambda record: self.log(record), ends=self._mission_ends)
        self._ids = itertools.count(1)
        self._top: dict[str, int] = {}  # session id -> the highest researcher number given in it (numbers count per session)
        self._loaded: set[str] = set()  # sessions whose researchers have been read from disk in this run
        self._worker_target = worker_target
        self._worker: ProcessChannel | None = None
        self._starting: asyncio.Task | None = None
        self._restarts = 0
        self._watchdogs: dict[str, asyncio.Task] = {}
        self._snapshots: dict[str, dict] = {}
        # Big deliverables proposed to the user and waiting for their yes (propose / take_proposal), by id.
        self.proposals: dict[str, dict] = {}
        self._proposal_ids = itertools.count(1)
        self._interaction_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._load_session(self.session(), start=True)
        self.spending = DayLedger(self.root, self.today, lambda: self.sessions.values())
        from .presentation import PresentationJobs
        self.presentations = PresentationJobs(self)
        from .. import drafts
        drafts.recover()

    # --- the session's researchers ----------------------------------------------------------
    def session_event(self, event: dict) -> None:
        """The session store's listener (app.py): another session opened (a new one, or one switched to), so its
        researchers are read, from every date it touched. Those still working, of any session, stay in memory and
        keep running: their work is filed in the session they were started in."""
        if event.get("type") == "session-opened" and event.get("session"):
            self._load_session(str(event["session"]))

    def _load_session(self, sid: str, start: bool = False) -> None:
        """A session's researchers come back (done, partial, stopped answering, failed, stopped; one that was running
        when the app stopped comes back "interrupted"), with the numbers they had, so "Researcher 8" means the same one
        in the session for good, and their work stays on the shelf to show again by voice or chat. At start, today's
        researchers of any session that were working when the app stopped come back too, so what their cut-off runs
        cost is settled and counted, never lost. Their spending is already counted."""
        if not sid or sid in self._loaded:
            return
        self._loaded.add(sid)
        found = [s for date in self._dates_of(sid) for s in self._read_folder(date, kept_only=False, session=sid)
                 if s.rid not in self.sessions]
        loaded = self._renumber(found, taken={s.n for s in self.sessions.values() if s.sitting == sid})
        if start:
            # One working when the app stopped (its saved status still says so): back as "interrupted".
            working = [s for s in self._read_folder(self.today(), kept_only=False, working=True)
                       if s.sitting != sid and s.rid not in self.sessions]
            loaded += self._renumber(working)
        for session in sorted(loaded, key=lambda x: x.created):
            interrupted_jobs = False
            for usage in session.usage_runs.values():
                job = usage.get('job', {})
                if job.get('status') in ('queued', 'dispatched'):
                    job.update(status='interrupted', cause='restart', completedAt=time.time())
                    if job.get('dispatchedAt'):
                        estimate = max(usage.get('estimated', 0.0), job.get('reservation', 0.0) - usage.get('spent', 0.0))
                        session.spent_estimated += estimate - usage.get('estimated', 0.0)
                        usage.update(estimated=estimate, outcomeUnknown=True)
                    interrupted_jobs = True
            for job in session.presentation_jobs:
                if job.get('state') in ('queued', 'preparing'):
                    job.update(state='interrupted', error='Interrupted by restart; no automatic regeneration')
                    if job.get('dispatchedAt') and not job.get('receipt'):
                        job.update(estimatedCost=job.get('reservation', 0.0), outcomeUnknown=True)
                    interrupted_jobs = True
            for job in session.interaction_jobs:
                if job.get('state') in ('admitted', 'dispatched'):
                    unknown = job['state'] == 'dispatched'
                    job.update(state='interrupted', outcome={'ok': False,
                        'code': 'OUTCOME_UNKNOWN' if unknown else 'INTERRUPTED',
                        'error': 'Interrupted; provider outcome is unknown. No automatic retry.' if unknown
                        else 'Interrupted before dispatch. Submit a new request to continue.'})
                    interrupted_jobs = True
            if session.renumbered or interrupted_jobs:
                self._save(session)  # a number given once (an old duplicate) is kept from now on
            self.sessions[session.rid] = session
        self._top[sid] = max([self._top.get(sid, 0)] + [s.n for s in self.sessions.values() if s.sitting == sid]
                             + self._numbers_on_disk(sid))

    def _dates_of(self, sid: str) -> list[str]:
        """The calendar dates a session touched, whose folders hold its researchers: the store's for the one open
        (today's included), the session list's for another, and always its own date (a day journal from before
        sessions is the session whose id is its date)."""
        dates = {sids.date_of(sid)} if sids.is_sid(sid) else set()
        store = self.store
        if store is not None and sid == store.sid:
            dates.update(store.days)
            dates.add(store.date)
        elif store is not None:
            info = store.catalogue.get(sid)
            dates.update(info.days if info is not None else [])
        elif sid == self.session():
            dates.add(self.today())
        return sorted(d for d in dates if isinstance(d, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d))

    def session_name(self, sid: str) -> str:
        """What a session is called, for the user: its name, else its date ("Oct 11")."""
        store = self.store
        if store is not None and sid == store.sid:
            return store.label
        info = store.catalogue.get(sid) if store is not None else None
        if info is not None:
            return info.label
        return sids.label(sid) if sids.is_sid(sid) else str(sid or "another session")

    def _next_number(self, sid: str) -> int:
        """The number a new researcher of session `sid` takes: one past the highest of any researcher started in it,
        on any date (scripted ones included); each session starts at Researcher 1."""
        if sid not in self._top:
            self._top[sid] = max([s.n for s in self.sessions.values() if s.sitting == sid] + self._numbers_on_disk(sid)
                                 + [0])
        self._top[sid] += 1
        return self._top[sid]

    def _entry_words(self, sitting: str, eid: str, kind: str = "") -> tuple[str, str]:
        """An entry's own title and kind (the app's words) from the in-memory store that owns its id, as the index
        words them, or ("", kind): never a full index read here, on the event loop (§1g; re-check SF-a). Only an entry
        of the researcher's own session (`sitting`): another session's e3 is another thing."""
        from ..untrusted import inline

        try:
            from ..evidence import evidence_of
            from ..held import held_of
            from ..resources import hhmm, resources_of

            population = evidence_of(self.hub).get(eid)
            if population is not None and _of_session(population, sitting):
                return inline(population.title, 80), "population"
            clip = held_of(self.hub).clips.get(eid)
            if clip is not None and _of_session(clip, sitting):
                return inline(f"Held footage · {clip.seconds:g} s · {hhmm(clip.started_at)}", 80), "clip"
            result = resources_of(self.hub).results.get(eid, sitting) if sitting == self.session() else None
            if result is not None:
                return inline(result.get("title"), 80), "result"
        except Exception:
            pass
        return "", kind or next((k for form, k in ID_KINDS if form.fullmatch(eid)), "")

    def _revision_now(self, sitting: str, eid: str) -> int | None:
        """A population's revision now, for a record read in session `sitting` (C1); None for another session's or
        anything else."""
        if sitting != self.session():
            return None
        try:
            from ..evidence import evidence_of

            population = evidence_of(self.hub).get(eid)
        except Exception:
            return None
        return population.revision if population is not None and _of_session(population, sitting) else None

    def _numbers_on_disk(self, sid: str) -> list[int]:
        """Every researcher number used in a session, on any of its dates (scripted and description ones included)."""
        found = []
        for date in self._dates_of(sid):
            for path in (self.root / date).glob("*/session.json"):
                try:
                    d = json.loads(path.read_text())
                    if _sitting_of(d, date) == sid:
                        found.append(int(d.get("n") or 0))
                except (OSError, ValueError, TypeError, AttributeError):
                    pass
        return found

    def days(self) -> list[dict]:
        """Days with finished research on disk, newest first (the studio's session list)."""
        out = []
        for d in sorted((d for d in self.root.glob("????-??-??") if d.is_dir()), reverse=True):
            n = len(self.read_day(d.name)) if d.name != self.today() else len(
                [s for s in self.sessions.values() if s.purpose == "research" and s.day == d.name])
            if n or d.name == self.today():
                out.append({"day": d.name, "count": n})
        return out

    def read_day(self, day: str, kept_only: bool = True, session: str | None = None) -> list[Session]:
        """A date folder's researchers from disk (read-only copies), of every session or only of `session`, with the
        numbers they were given (Session.n is saved; only one saved without a number, or with a number another of its
        session already had, gets a new one after its session's highest). kept_only: just the finished ones that made
        something (an earlier day's list); otherwise every one, and one that was still working when the app stopped
        is "interrupted"."""
        return self._renumber(self._read_folder(day, kept_only, session=session))

    def _read_folder(self, day: str, kept_only: bool = True, session: str | None = None,
                     working: bool = False) -> list[Session]:
        """read_day without renumbering; `working`: only those whose saved status says they were working."""
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
            return []
        loaded = []
        for path in (self.root / day).glob("*/session.json"):
            try:
                d = json.loads(path.read_text())
                if (d.get("purpose") != "research" or d.get("effort") not in EFFORTS or d.get("engine") == "fake"  # scripted tests
                        or (kept_only and d.get("status") not in KEPT)):
                    continue
                sitting = _sitting_of(d, day)
                if (session is not None and sitting != session) or (
                        working and not (d.get("status") in ACTIVE or d.get("status") == "revising")):
                    continue
                if d.get("status") in ACTIVE or d.get("status") == "revising":
                    d["status"], d["step"] = "interrupted", "Stopped when the app restarted"
                # A follow-up answer or a new version that was going when the app stopped: its
                # earlier work stands, and its history says so (run.py).
                stopped = interrupted(d) if not kept_only else None
                if stopped is not None:
                    d["history"] = [*(d.get("history") or []), stopped]
                    d["step"] = ("Its new version was stopped when the app restarted; ask for it again" if stopped["mode"] == "revise"
                                 else "Its answer was stopped when the app restarted; ask again")
                    d["answering"] = d["revising"] = None
                # Questions kept while it worked, never asked before the app stopped: said, as an interrupted answer is.
                for kept in d.get("pending") or []:
                    if isinstance(kept, dict) and kept.get("question"):
                        d["history"] = [*(d.get("history") or []), {
                            "brief": str(kept["question"]), "mode": "answer", "status": "interrupted", "result": None,
                            "why": "the app restarted before it was asked", "at": kept.get("at")}]
                # Outlines from before the restart are gone: nothing points at an object id, and
                # suggestions nobody answered are no longer offered.
                corrections = [{**c, "object_id": None, "status": c.get("status") if c.get("status") != "proposed" else "expired"}
                               for c in d.get("corrections") or []]
                outputs = versions.number([{k: v for k, v in o.items() if k not in ("url", "current")} for o in d.get("outputs") or []])
                result = strip_citations(dict(d["result"])) if d.get("result") else None
                if result and result.get("details") and path.parent.parent.name < "2026-10-04":
                    # Results saved before 2026-10-04 were cut mid-word at 200 characters (only those: a later
                    # result is never changed by being read back).
                    result["details"] = [t if t[-1:] in ".!?)…\"'" or len(t) < 190 else t.rsplit(" ", 1)[0].rstrip(",;:") + "…"
                                         for t in result["details"] if isinstance(t, str) and t]
                if result and result.get("refs"):
                    result["refs"] = [{k: v for k, v in r.items() if k != "url"} for r in result["refs"]]
                kept = _kept_working(d)
                loaded.append(Session(
                    rid=d["rid"], n=int(d.get("n") or 0), brief=d.get("brief") or "", deliverable=d.get("deliverable"),
                    effort=d["effort"], engine=d.get("engine") or "", purpose="research", label=d.get("label") or "",
                    object_id=None, origin={**(d.get("origin") or {}), "delegation_id": None}, day=path.parent.parent.name,
                    deliver="tab" if d.get("deliver") == "tab" else None,
                    status=d.get("status") or "done", step=d.get("step") or "", step_at=d.get("stepAt"), spent=float(d.get("spent") or 0), created=float(d.get("created") or 0),
                    started=d.get("started"), finished=d.get("finished"), outputs=outputs, findings=strip_citations(d.get("findings") or []),
                    corrections=corrections, result=result, response_id=d.get("responseId"), history=strip_citations(d.get("history") or []),
                    finding_revisions=d.get("findingRecords") or [],
                    interaction_jobs=copy.deepcopy(d.get('interactionJobs') or []),
                    presentation_jobs=copy.deepcopy(d.get('presentationJobs') or []),
                    usage_runs=copy.deepcopy(d.get('usageRuns') or {}),
                    dependencies=copy.deepcopy(d.get('dependencies') or []),
                    evidence_reads=[w for w in d.get("evidenceReads", d.get("working") or []) if _working_ok(w)],
                    evidence_reads_missing=int(d.get("evidenceReadsMissing", d.get("workingDropped") or 0)),
                    has_crop=str(d.get("snapshot", "")).endswith("crop.jpg"), restored=True,
                    sitting=sitting, specimen=str(d.get("specimen") or ""),
                    spent_estimated=float(d.get("spentEstimated") or 0), root=self.root,
                    notes=_read_inbox(path.parent), plan=d.get("plan") if isinstance(d.get("plan"), dict) else None,
                    requests=[r for r in d.get("requests") or [] if isinstance(r, dict)],
                    working=kept[0], working_dropped=kept[1], revision_now=self._revision_now,
                    ran=[r for r in d.get("ran") or [] if _ran_ok(r)],
                    result_run=d.get("resultRun") if isinstance(d.get("resultRun"), dict) else None,
                    admission=d.get("admission") if isinstance(d.get("admission"), dict) else None,
                    asking=str(d.get("asking") or ""),
                    mission=Mission.from_wire(d["rid"], d["mission"]) if isinstance(d.get("mission"), dict) else None,
                    past_missions=[m for m in (Mission.from_wire(d["rid"], x) for x in d.get("pastMissions") or []
                                               if isinstance(x, dict)) if m is not None]))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        if kept_only:
            loaded = [x for x in loaded if x.outputs or x.result]
        return loaded

    @staticmethod
    def _renumber(loaded: list[Session], taken: set[int] | None = None) -> list[Session]:
        """Saved numbers are kept. Before 2026-10-04 a restart numbered afresh, so a day (then its one session) can
        have one number twice: the first made keeps it, a later one gets the next free number in its session. Numbers
        count per session, so two sessions' Researcher 1 are two researchers, never renumbered."""
        by_session: dict[str, list[Session]] = {}
        for session in loaded:
            by_session.setdefault(session.sitting, []).append(session)
        for group in by_session.values():
            used: set[int] = set(taken or ())
            top = max([s.n for s in group] + list(used) + [0])
            for session in sorted(group, key=lambda x: x.created):
                if session.n <= 0 or session.n in used:
                    top += 1
                    session.n, session.renumbered = top, True
                used.add(session.n)
        return loaded

    def spent_today(self) -> float:
        return self.spending.spent()

    def estimated_today(self) -> float:
        """The part of spent_today() that is estimated (no receipt yet), of every researcher: the studio says it apart
        ("up to … more, receipts pending"), so Research today matches Spending's recorded charges."""
        return self.spending.estimated()

    @staticmethod
    def working(session: Session) -> bool:
        """Still working: a run going (or queued), or a mission whose slots the app is still running."""
        mission = session.mission
        return session.status in ACTIVE or (mission is not None and mission.status in ("watching", "checkpoint", "finishing"))

    def list_wire(self) -> dict:
        """The researchers the studio lists (the open session's, and those still working in other sessions: each says
        its session, `sitting`), the descriptions, and today's spend."""
        now = self.session()
        return {"sessions": [s.wire() for s in self.sessions.values()
                             if s.purpose != "research" or s.sitting == now or self.working(s)],
                "spentToday": round(self.spent_today(), 4), "estimatedToday": round(self.estimated_today(), 4)}

    def working_by_session(self) -> dict[str, int]:
        """How many researchers are working in each session ({sid: n}; the session list's "working")."""
        counts: dict[str, int] = {}
        for s in list(self.sessions.values()):
            if s.purpose == "research" and self.working(s):
                counts[s.sitting] = counts.get(s.sitting, 0) + 1
        return counts

    def researchers(self, day: str | None = None, *, session: str | None = None) -> list[Session]:
        """The researchers (not the brain's own corrections or labels' descriptions) of a session, oldest first: the
        one open now, else `session`, from every date it touched (one not read in this run is read from disk, as
        read-only copies); with `day`, only those filed in that date's folder. Safe off the event loop."""
        sid = session or self.session()
        here = [s for s in list(self.sessions.values()) if s.purpose == "research" and s.sitting == sid]
        if sid not in self._loaded:
            known = {s.rid for s in here}
            here += [s for s in self._renumber([s for date in self._dates_of(sid)
                                                for s in self._read_folder(date, kept_only=False, session=sid)])
                     if s.rid not in known]
        return sorted((s for s in here if day is None or s.day == day), key=lambda s: s.created)

    def lines(self, limit: int | None = 6) -> str:
        """For the brain: this session's researchers in full (the newest `limit`, the rest counted; None: all). Other
        sessions' work is theirs (ADR 0017): the session open is told one line when one of theirs finishes
        (announcer.py), never their work."""
        research = self.researchers()
        current = research if limit is None else research[-limit:]
        lines = [s.line() for s in current]
        if len(research) > len(current):  # never a silent cut: the rest are one call away
            lines.append(f"(and {len(research) - len(current)} earlier in this session, not listed here: research_status "
                         "lists them)")
        return "\n".join(lines)

    def running(self, pool: str) -> list[Session]:
        return [s for s in self.sessions.values() if s.status in ("starting", "running") and EFFORTS[s.run_effort].pool == pool]

    def get(self, rid: str) -> Session | None:
        return self.sessions.get(rid)

    def attempt_ref(self, session: Session) -> dict:
        """An actual admitted run identity; restored history cannot mint a new attempt."""
        attempt = session._attempt
        if attempt is None:
            raise ResearchRefused("that researcher has no retained original run to wait for", next=None)  # no next: historical work cannot reconstruct a live attempt identity
        return {"kind": "research-run", "researcherId": session.rid, "day": session.day,
                "runId": attempt.id, "run": attempt.number}

    def resolve_attempt(self, ref: dict) -> tuple[Session, WorkerAttempt]:
        run_keys = {"kind", "researcherId", "day", "runId", "run"}
        result_keys = run_keys | {"status", "resultSha256", "outputs"}
        if (not isinstance(ref, dict) or ref.get("kind") not in {"research-run", "research-result"}
                or set(ref) != (run_keys if ref["kind"] == "research-run" else result_keys)
                or any(not isinstance(ref.get(k), str) or not ref[k] or len(ref[k]) > 256
                       for k in ("researcherId", "day", "runId"))
                or type(ref.get("run")) is not int or ref["run"] < 0):
            raise ResearchRefused("invalid original researcher run reference", next=None)  # no next: malformed identity cannot select an original producer
        session = self.get(ref["researcherId"])
        attempt = session._attempt if session is not None else None
        if (session is None or session.day != ref["day"]
                or attempt is None or attempt.id != ref["runId"] or attempt.number != ref["run"]):
            raise ResearchRefused("the original researcher run was replaced", next=None)  # no next: a newer attempt cannot satisfy the bound original run
        self._this_session_only(session)
        if ref["kind"] == "research-result":
            if not attempt.completion.done() or attempt.completion.cancelled():
                raise ResearchRefused("that original researcher result has not completed", next=None)  # no next: this result reference has no successful original terminal completion
            outcome = attempt.completion.result()
            if ref != self.result_ref(session, attempt, outcome):
                raise ResearchRefused("the original researcher result or output version changed", next=None)  # no next: changed terminal facts cannot satisfy the original immutable reference
            # A follow-up answer/revision intentionally preserves the main result.
            # Qualify this completion against its own output snapshots, not that
            # different result. Current renderability is checked by the consumer.
            produced = outcome.get("producedOutputs", [])
            if any(session.output(output.get("id")) != output for output in produced):
                raise ResearchRefused("the original researcher result or output version changed", next=None)  # no next: current outputs no longer match the original produced snapshots
            if outcome.get("mode") is None and self.result_hash(session.result) != ref["resultSha256"]:
                raise ResearchRefused("the original researcher result changed", next=None)  # no next: changed main work cannot satisfy its original result hash
        return session, attempt

    def result_view(self, ref: dict) -> Session:
        """Read only the actual original completion, without borrowing older main work."""
        session, attempt = self.resolve_attempt(ref)
        if ref["kind"] != "research-result":
            raise ResearchRefused("reading original completed work needs its successful result reference", next=None)  # no next: an attempt identity alone supplies no successful completed reading
        outcome = attempt.completion.result()
        view = copy.copy(session)
        view.result = copy.deepcopy(outcome.get("result"))
        view.outputs = copy.deepcopy(outcome.get("producedOutputs", []))
        view.status = outcome["status"]
        view.origin = copy.deepcopy(attempt.origin)
        view.result_run = copy.deepcopy(outcome["run"])
        view.history, view.last = [], None
        return view

    @staticmethod
    def result_hash(result) -> str:
        return hashlib.sha256(json.dumps(result, ensure_ascii=False, allow_nan=False,
                                        sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def result_ref(self, session: Session, attempt: WorkerAttempt, outcome: dict) -> dict:
        if (outcome.get("status") != "done" or outcome.get("run", {}).get("runId") != attempt.id
                or not (outcome.get("result") or outcome.get("outputs"))):
            raise ResearchRefused("the original researcher run did not complete successfully", next=None)  # no next: failed, empty or different-run work cannot produce this successful result reference
        return {"kind": "research-result", "researcherId": session.rid, "day": session.day,
                "runId": attempt.id, "run": attempt.number, "status": "done",
                "resultSha256": self.result_hash(outcome.get("result")),
                "outputs": copy.deepcopy(outcome.get("outputs", []))}

    async def wait_attempt(self, session: Session, attempt: WorkerAttempt, guard: Callable[[], None]) -> dict:
        """Wait on the original producer without cancelling it when this waiter retires."""
        while True:
            guard()
            if session.sitting != self.session():
                raise ResearchRefused("another session was opened while waiting for the original researcher", next=None)  # no next: this waiter belongs to the original session and cannot migrate to another
            if attempt.completion.done():
                if attempt.completion.cancelled():
                    raise ResearchRefused("the original researcher completion is unavailable", next=None)  # no next: the original producer's canceled completion cannot be reconstructed by waiting
                outcome = copy.deepcopy(attempt.completion.result())
                if outcome.get("status") != "done":
                    raise ResearchRefused(f"original researcher run ended {outcome.get('status')}; cause {outcome.get('cause')}", next=None)  # no next: an unsuccessful terminal outcome cannot fulfill this success dependency
                return outcome
            # As long as it works (it is heard from), however long: the wait ends with it, or when the app stops hearing
            # from it (the watchdog's own measure, WATCHDOG_SILENT_S), never at a clock the work did not set.
            remaining = WATCHDOG_SILENT_S + 30.0 - (time.monotonic() - (session._heard or time.monotonic()))
            if remaining <= 0:
                raise ResearchRefused("the original researcher has no confirmed terminal result within this wait", next=None)  # no next: this bounded wait has expired; it neither extends nor cancels the independent producer
            # asyncio.wait never cancels its supplied Future on timeout or waiter cancellation.
            await asyncio.wait([attempt.completion], timeout=min(0.05, remaining))

    def corrections(self, status: str | None = "proposed") -> list[tuple[Session, dict]]:
        return [(s, c) for s in self.sessions.values() for c in s.corrections if status is None or c.get("status") == status]

    def correction(self, cid: str) -> tuple[Session, dict] | None:
        return next(((s, c) for s, c in self.corrections(None) if c.get("id") == cid), None)

    def propose(self, session: Session, object_id: str | None, to_label: str, reason: str, confidence=None,
                sources: list | None = None) -> dict:
        """A suggested relabelling (from a researcher, or from the brain). Nothing changes on
        screen until the user accepts it."""
        correction = {"id": f"{session.rid}-c{len(session.corrections) + 1}", "object_id": object_id,
                      "to_label": to_label, "reason": reason, "confidence": confidence,
                      "sources": list(sources or []), "status": "proposed", "from": session.name,
                      "label": (self.hub.scene.objects[object_id].label if object_id in self.hub.scene.objects else session.label)}
        crop = self._keep_correction_crop(session, correction["id"], object_id)
        if crop:
            correction["crop"] = crop  # the thing as it looked when it was suggested (bench/export_corrections.py)
        session.corrections.append(correction)
        self._ledger(session, {"kind": "correction", **correction})
        self._save(session)
        self._changed(session)
        for hook in self.on_correction:
            hook(session, correction)
        return correction

    def _keep_correction_crop(self, session: Session, cid: str, object_id: str | None) -> str | None:
        """<day>/<rid>/corrections/<cid>.jpg: the object's sharpest recent frame, so an accepted
        correction becomes a labelled picture (also for the brain's own proposals, which have no
        researcher snapshot). Relative to the research folder; None when there is no picture."""
        obj = self.hub.scene.objects.get(object_id) if object_id else None
        if obj is None or self.root is None:
            return None
        try:
            found = crop_jpeg(self.hub, obj, pad=0.3)
            if found is None:
                return None
            path = self.root / session.day / session.rid / "corrections" / f"{cid}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(found[0])
            return str(path.relative_to(self.root))
        except Exception:
            return None

    def decide(self, cid: str, accept: bool) -> tuple[Session, dict] | None:
        found = self.correction(cid)
        if found is None or found[1].get("status") != "proposed":
            return None
        session, correction = found
        correction["status"] = "accepted" if accept else "dismissed"
        self._ledger(session, {"kind": "decision", "correction": cid, "by": "user", "status": correction["status"]})
        self._save(session)
        self._changed(session)
        return found

    def brain_session(self) -> Session:
        """Where the brain's own proposals are recorded (not a researcher; never run)."""
        session = self.sessions.get("brain")
        if session is None:
            session = Session(rid="brain", n=0, brief="", deliverable=None, effort="instant", engine="", purpose="brain",
                              label="", object_id=None, origin={}, day=self.today(), status="done",
                              sitting=self.session())
            self.sessions["brain"] = session
        return session

    def target_output(self, session: Session, target: str | None = None) -> dict:
        """The current version of the thing `target` names: an output id ("o3", any version of
        it), "lesson"/"quiz", "picture", "labels", "illustration" or "video"; None: its main
        work (the most substantial current output). Raises ResearchRefused."""
        versions.number(session.outputs)
        current = session.current_outputs()
        text = " ".join(str(target or "").lower().split())
        if re.fullmatch(r"o\d+", text):
            found = versions.head(session.outputs, text)
            if found is None:
                raise ResearchRefused(f"{session.name} made nothing called {text}", next="say which of its work to change")
            return found
        if not current:
            raise ResearchRefused(f"{session.name} has made nothing to change yet", next="wait until it has made something")
        if not text:
            # Its main work that a new version can be made of (a presentation is restyled, never revised here).
            main = next((o for o, now in versions.ordered(session.outputs) if now and versions.kind_name(o) in REVISABLE), None)
            if main is None:
                raise ResearchRefused(f"{session.name} made nothing it can make a new version of",
                                      next="ask a new researcher for it instead")
            return main
        listed = ", ".join(f"{o['id']} ({versions.kind_name(o)})" for o in current)
        revisable = [o for o in current if versions.kind_name(o) in REVISABLE]
        if len(revisable) == 1:
            return revisable[0]  # the only thing a new version can be made of: no word decides
        wanted = versions.kind_meant(text)
        if wanted is None:
            raise ResearchRefused(f"I don't know which of {session.name}'s work {target!r} is",
                                  next=f"name it by its id: {listed}")
        found = [o for o in current if versions.fits(o, wanted)]
        if not found:
            raise ResearchRefused(f"{session.name} made no {wanted}", next=f"name it by its id: {listed}")
        # Words decided, not the model's id: said in the log as code choosing for the model (facts-not-verdicts).
        self._log(session, {"kind": "override", "what": "revise target by words", "words": text, "chose": found[-1]["id"]})
        return found[-1]  # the newest

    def _admit_mode(self) -> str:
        """How a request for a big deliverable is told apart (option ADMIT; policy.explicit_refusal)."""
        return getattr(self.hub.settings, "admit", "quote")

    async def interact(self, rid: str, request_id: str, payload: dict, dispatch: Callable) -> dict:
        """One durable composer submission, before any provider or inbox dispatch.

        The session remains the persistence owner. Replays read the same outcome;
        an uncertain dispatch is never retried. This serializes admission within
        the sole research-manager process, not across the duration of other jobs.
        """
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', request_id):
            raise ResearchRefused('invalid research request identity', next=None)  # no next: protocol identity is supplied by the client, never typed by the user
        session = self.sessions.get(rid)
        if session is None or session.purpose != 'research':
            raise ResearchRefused('this researcher is unavailable', next='open a researcher of this session to continue working')
        self._this_session_only(session)
        payload = copy.deepcopy(payload)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        lock = self._interaction_locks.setdefault((rid, request_id), asyncio.Lock())
        async with lock:
            existing = next((job for job in session.interaction_jobs if job['id'] == request_id), None)
            if existing is not None:
                if existing['fingerprint'] != fingerprint:
                    raise ResearchRefused('this request identity was used for different content', next='inspect the original request before submitting a new one')
                return copy.deepcopy(existing.get('outcome') or {'ok': False, 'code': 'OUTCOME_UNKNOWN',
                    'error': 'The original dispatch has no confirmed outcome. It will not be repeated.'})
            current = (session.result or {}).get('findingRevision', {}).get('version')
            if 'expectedBase' in payload and payload['expectedBase'] != current:
                raise ResearchRefused('the findings changed since this request was composed',
                                      next='inspect the new version and submit again')
            job = {'id': request_id, 'fingerprint': fingerprint, 'request': payload,
                   'created': time.time(), 'state': 'admitted'}
            session.interaction_jobs.append(job)
            try:
                self._save(session)
            except BaseException:
                session.interaction_jobs.remove(job)
                raise
            job['state'] = 'dispatched'
            self._save(session)  # must reach disk before calling anything that can incur a charge
            self._changed(session)
            try:
                outcome = await dispatch()
            except asyncio.CancelledError:
                job.update(state='interrupted', outcome={'ok': False, 'code': 'OUTCOME_UNKNOWN',
                    'error': 'Interrupted; provider outcome is unknown. No automatic retry.'})
                self._save(session)
                self._changed(session)
                raise
            except Exception as exc:
                job.update(state='failed', outcome={'ok': False, 'error': str(exc)})
                self._save(session)
                self._changed(session)
                raise
            job.update(state='completed', outcome=copy.deepcopy(outcome), finished=time.time())
            try:
                self._save(session)
            except OSError:
                job.update(state='interrupted', outcome={'ok': False, 'code': 'PERSISTENCE_FAILED',
                    'error': 'The operation returned but its outcome could not be saved. Do not repeat the request.'})
                self._changed(session)
                raise
            self._changed(session)
            return copy.deepcopy(outcome)

    async def followup(self, rid: str, question: str, origin: dict | None = None, mode: str = "answer",
                       target: str | None = None, change: str | None = None, words: str = "", asked_with: str = "",
                       delivery: Delivery | None = None, selection: dict | None = None,
                       edit_fields: list[str] | None = None, effort: str | None = None, confirmed: bool = False) -> Session:
        """Ask a researcher that has stopped (done, partial, stopped answering, restored today) a
        further question, or to change something it made.

        mode "answer" (the default): a cheap answer about its finished work. It runs at quick
        effort with no picture, video or lesson tools; its brief is the question plus the work
        so far (readable(), marked untrusted). The answer is added to `history` and given to the
        on_done hooks as `session.answered`; the work, its result and status stay as they were.
        mode "continue": it carries on where it left off (the Responses chain for gpt-6.1-sol)
        at the same effort and deliverable, and the new result replaces the old one (the old one
        moves to history).
        mode "revise": a new version of one thing it made (`target`, see target_output; `change`
        says what to change, else the question): a lesson's content is edited (only what the
        change is about; steps it does not name are copied as they were) and its page written
        again; labels are renamed or removed without a model when the change says so plainly,
        otherwise edited and drawn again on the original photo; an illustration is edited from
        its current version; a video is made again from the photo with the change added to its
        motion. REVISE_EFFORT (a video: standard). The new output has `replaces` (the old id),
        `version` and `change`; the old one stays. Given to the on_done hooks as
        `session.revised`, and recorded in `history` (mode "revise"). The result is unchanged.

        `words`: what the user said in the last half minute (policy.admit: "continue" and a new
        version of a video need an explicit request), and the day's cap is checked."""
        if mode not in ("answer", "continue", "revise"):
            raise ResearchRefused(f"unknown follow-up mode {mode!r}", next=None)  # no next: the model's own call; it corrects it
        session = self.sessions.get(rid)
        if session is None or session.purpose != "research":
            raise ResearchRefused("there is no such researcher", next="say which researcher, or look in the Research tab")
        self._this_session_only(session)
        if mode in ('continue', 'revise'):
            from .selection import require_current
            require_current(session, selection)
        if session.status in ACTIVE and mode == "answer":
            # Kept, not refused: it answers from its work as soon as this run ends (what it has found so far is read
            # now with read_result, which works while it works).
            session.pending.append({"question": " ".join(str(question or "").split()), "origin": copy.deepcopy(origin),
                                    "words": words, "asked_with": asked_with, "selection": copy.deepcopy(selection),
                                    "at": round(time.time(), 2)})
            session._pending_delivery = delivery
            self._save(session)
            self._changed(session)
            return session
        if session.status in ACTIVE:
            raise ResearchRefused(f"{session.name} is still working: I can pass it a note now, or ask again when it has finished", next=None)  # no next: the message says what to do
        before_admission = self._admission_state(session)
        question = _whole(" ".join(str(question or "").split()), "the question")
        change = _whole(" ".join(str(change or question).split()), "the change")
        if not (change if mode == "revise" else question):
            raise ResearchRefused("no change asked for" if mode == "revise" else "no question to ask", next="say what you want to ask or change")
        cap = self.hub.settings.research_cap
        if mode == "revise":
            output = self.target_output(session, target)
            kind = versions.kind_name(output)
            if kind not in REVISABLE:  # refused before anything is admitted or reserved
                raise ResearchRefused(f"{session.name}'s {kind} can't be changed by a new version",
                                      next="say which of its work to change")
            if edit_fields is not None and (kind != 'lesson' or not isinstance(edit_fields, list)
                    or not 1 <= len(edit_fields) <= 3
                    or any(field not in ('prompt', 'answer', 'hint') for field in edit_fields)
                    or len(set(edit_fields)) != len(edit_fields)):
                raise ResearchRefused('lesson edits need distinct prompt, answer or hint fields', next=None)  # no next: malformed model-selected edit scope cannot authorize a revision
            admitted = admit("revise", words=words, target_kind=kind, asked_with=asked_with, mode=self._admit_mode())
            self.spending.check(cap, EFFORTS[admitted.effort].cap_usd)
            content = session.content_of(output)
            if kind == "lesson" and not (content or {}).get("steps"):
                raise ResearchRefused(f"{session.name}'s lesson could not be read, so it cannot be changed; ask for a new one", next=None)  # no next: the message says what to do
            if kind == "picture" or (kind == "labelled picture" and not (content or {}).get("items")):
                # A labelled photo saved without its labels: changing it would mean redrawing the specimen.
                raise ResearchRefused(f"{session.name}'s picture has no labels it can change", next="ask for a new labelled picture instead")
            session.last = None
            session.run = Run("revise", session.status, admitted.effort, change, kind_name=kind,
                              target={k: output.get(k) for k in ("id", "kind", "path", "caption", "generated", "version")}
                              | {"content": content, 'findingRevision':copy.deepcopy(output.get('findingRevision')),
                                 'anchor':(selection or {}).get('anchor'), 'edit_fields':copy.deepcopy(edit_fields)})
            return self._requeue(session, origin, delivery=delivery, rollback=before_admission)
        if effort is not None and (mode != "continue" or effort not in CHOOSABLE):
            raise ResearchRefused(f"a continue's effort is one of {', '.join(CHOOSABLE)}", next=None)  # no next: the model's own argument
        # "Go deeper on that": a continue at the effort the user asked for, admitted as a start at it would be.
        admitted = admit(mode, words=words, effort=(effort or session.effort) if mode == "continue" else session.effort,
                         deliverable=session.deliverable, asked_with=asked_with, mode=self._admit_mode(), confirmed=confirmed)
        self.spending.check(cap, EFFORTS[admitted.effort].cap_usd)
        session.last = None
        if mode == "answer":
            from .selection import view as selected_view
            from research_worker.limits import BRIEF_MAX

            view = selected_view(session, selection)
            whole_work = view.readable(limit=None)
            if len(whole_work) + len(question) + 200 > BRIEF_MAX:  # its summary whole, the rest read by id
                whole_work = (view.readable("summary", limit=None)
                              + f"\n(Its sources, earlier questions and what it made are left out here to fit: the session "
                                f"tool reads them whole: action read, id {session.rid}.)")
            work = block(whole_work, tag="earlier_work", source="researcher")
            session.run = Run("answer", session.status, admitted.effort,
                              f"Follow-up question from the user: {question}\n\n{work}", question=question)
            return self._requeue(session, origin, delivery=delivery, rollback=before_admission)
        return self._continue(session, question, origin, delivery=delivery, effort=admitted.effort)

    def _continue(self, session: Session, question: str, origin: dict | None,
                  mission: Mission | None = None, *, delivery: Delivery | None = None, effort: str | None = None) -> Session:
        """An already admitted continue, preserving the preceding finding's actual producing facts."""
        before_admission = self._admission_state(session)
        session.last = None
        # Built from the finding it continues, before that finding moves to history: the same text on every engine.
        package = self._continuation(session, question)
        # The run that ends here, kept whole; `next` is the question the continue that starts now answers (what it
        # reads is said by it: Session.working_lines).
        session.history.append({"brief": session.asking or session.brief, "result": session.result, "status": session.status,
                                "next": question, "started": session.started, "finished": session.finished,
                                "plan": copy.deepcopy(session.plan),
                                "version": copy.deepcopy((session.result or {}).get("findingRevision")),
                                **copy.deepcopy(session.result_run or {})})
        session.asking, session.package = question, package  # `brief` stays the original request
        if effort:
            session.effort = effort  # its work goes on at the effort this continue was admitted at
        session.result, session.finished = None, None
        session.result_run = None
        session.response_id = None  # no cross-run chain: every engine continues from the package alone
        return self._requeue(session, origin, mission, delivery=delivery, rollback=before_admission)

    def _continuation(self, session: Session, question: str) -> str:
        """What a continue is given, the same on every engine (the researcher repair, Phase 3): the original request,
        the finding it continues (whole, named by its version), its open questions, its earlier follow-up answers
        and an index of its earlier runs, then the new request. Within limits.BRIEF_MAX: the earlier runs' index and
        the follow-up answers are left out whole (said, with where to read them) before anything required; a
        continue whose required parts alone are over it is refused, never cut."""
        from research_worker.limits import BRIEF_MAX

        r = session.result or {}
        revision = r.get("findingRevision") or {}
        version = f" (its finding, version {revision['version']})" if revision.get("version") else ""
        read_it = f"the session tool reads it whole: action read, id {session.rid}"
        open_questions = [q for q in session.requests if q.get("status") in ("asked", "unanswered")]
        answered = [q for q in session.requests if q.get("status") == "answered"]
        follow_ups = [h for h in session.history if h.get("mode") == "answer"]
        earlier = [h for h in session.history if not h.get("mode")]
        required = [
            "You are carrying on your own earlier work. Build on what it found; do not repeat what it settled; take up "
            "what it could not settle.",
            f"The original request: {session.brief}",
            f"What you found so far{version}:\n" + (session.readable("summary", limit=None) if r else "(no written result)"),
        ]
        if open_questions or answered:
            required.append("Your questions to the user: " + "; ".join(
                [f"{q['id']} ({q.get('what')}): still open" for q in open_questions]
                + [f"{q['id']} ({q.get('what')}): answered \"{q.get('answer')}\"" for q in answered]))
        optional = []
        if follow_ups:
            optional.append(("the follow-up questions answered from it", "Follow-up questions answered from it:\n" + "\n".join(
                f"- {h.get('brief')}: {(h.get('result') or {}).get('summary') or h.get('status')}" for h in follow_ups)))
        if earlier:
            optional.append(("the index of its earlier runs", "Its earlier runs (" + read_it + "):\n" + "\n".join(
                f"- asked: {h.get('brief')}; found: {(h.get('result') or {}).get('title') or h.get('status')}" for h in earlier)))
        last = f"Now, the user asks: {question}"
        size = sum(len(x) + 2 for x in required) + len(last)
        if size > BRIEF_MAX:
            raise ResearchRefused(f"{session.name}'s finding and this request are {size:,} characters together; at most "
                                  f"{BRIEF_MAX:,} can be given whole, and none is cut",
                                  next="ask a new researcher for the next step, giving it this one's work to build on")
        kept, left_out = [], []
        for name, text in optional:
            if size + len(text) + 2 <= BRIEF_MAX:
                kept.append(text)
                size += len(text) + 2
            else:
                left_out.append(name)
        if left_out:
            kept.append(f"Left out here to fit, never cut: {', '.join(left_out)} ({read_it}).")
        return "\n\n".join(required + kept + [last])

    @staticmethod
    def _admission_state(session):
        keys = ('status', 'step', 'run', 'last', 'origin', '_delivery', '_attempt', '_notes_from', '_before',
                'result', 'finished', 'result_run', 'history', 'brief', 'asking', 'package', 'response_id', 'usage_runs',
                'effort', 'plan')
        return {key: (getattr(session, key) if key in ('_delivery', '_attempt') else copy.deepcopy(getattr(session, key)))
                for key in keys}

    def _new_attempt(self, session: Session, mission: Mission | None = None) -> WorkerAttempt:
        attempt = WorkerAttempt(session.run_number(), copy.deepcopy(session.origin), mission,
                                mission.status if mission is not None else None, delivery=session._delivery)
        attempt.record = {**attempt.facts(), "status": "queued", "reservation": EFFORTS[session.run_effort].cap_usd,
                          "configuredEngine": session.engine}
        session._attempt = attempt
        # Charged to the calendar day it starts on, whichever date folder its researcher is in (ledger.py).
        session.usage_runs[attempt.id] = {'spent': 0.0, 'estimated': 0.0, 'sequence': -1, 'day': self.today(),
                                        'job': attempt.record}
        if mission is not None:
            mission.model_runs.append(attempt.record)
        return attempt

    def _requeue(self, session: Session, origin: dict | None, mission: Mission | None = None, *,
                 delivery: Delivery | None = None, rollback: dict | None = None) -> Session:
        """Queue a stopped researcher for another run (a follow-up), returning at once."""
        session.status, session.step = "queued", ""
        if mission is not None:
            session.origin = copy.deepcopy(mission.origin)  # missing original fields stay unknown, never latest-thread defaults
        elif origin:
            # Who asked this time (and on which delegation); a thread the caller set stays.
            session.origin = {**({"thread": session.origin["thread"]} if (session.origin or {}).get("thread") else {}), **origin}
        session._delivery = delivery  # including unknown: never inherit a previous request's voice
        self._new_attempt(session, mission)
        session._notes_from = len(session.notes)
        if session.mission is not None and session.mission.notes_from is not None:
            session._notes_from = min(session._notes_from, session.mission.notes_from)
            session.mission.notes_from = None
        session._before = {"spent": session.spent, "estimated": session.spent_estimated, "outputs": len(session.outputs),
                           "findings": len(session.findings)}
        self.spending.hold(session, EFFORTS[session.run_effort].cap_usd)
        # What a new run is told (specimen and conversation now, how to explain), with the object it is about; never
        # "not described yet" for a follow-up because only the learner line was passed.
        facts, _info = self._snapshot_context()
        facts.pop("frame_id", None)
        if session.object_id:
            facts["object"] = {"id": session.object_id, "label": session.label}
        self._snapshots[session.rid] = facts
        try:
            # A researcher filed on an earlier date running today: listed in today's folder before it can spend, so
            # today's spend finds it after a restart (ledger.py).
            self.spending.note_run(session.rid, session.day, session.usage_runs[session._attempt.id]["day"])
            self._save(session)
        except OSError:
            self.spending.release(session.rid)
            self._snapshots.pop(session.rid, None)
            if rollback is not None:
                for key, value in rollback.items():
                    setattr(session, key, value)
            self._changed(session)
            raise
        self.queue.append(session.rid)
        self._changed(session)
        self._pump()
        return session

    def note(self, rid: str, text: str, answers: str | None = None, about: dict | None = None) -> Session:
        """A note from the user to a researcher that is working ("focus on the stomata", "keep
        it short"): added to its inbox (inbox.jsonl in its folder, kept), and read by the worker
        before its next round as a message from the user, including between a mission's checkpoints. The app's own marks ride
        with it for the worker (never the model's words): which question it answers, footage being held for it
        (`about` {"holding": k1} or {"held": k1}), so a finish can wait for what is still to come."""
        session = self.sessions.get(rid)
        if session is None or session.purpose != "research":
            raise ResearchRefused("there is no such researcher", next="say which researcher, or look in the Research tab")
        self._this_session_only(session)
        idle_mission = session.mission is not None and session.mission.status in ("watching", "checkpoint", "finishing")
        if session.status not in ACTIVE and not idle_mission:
            raise ResearchRefused(f"{session.name} has finished, so a note can't reach it",
                                  next="ask it with researcher_followup, or change what it made with revise_work")
        if session.run is not None and session.run.kind == "revise":  # one edit call: no next step to read a note at
            raise ResearchRefused(f"{session.name} is making its new version; when it is ready, ask for another change", next=None)  # no next: the message says what to do
        text = _whole(" ".join(str(text or "").split()), "the note")
        if not text:
            raise ResearchRefused("no note to pass on", next=None)  # no next: the model's own call; it corrects it
        request = None
        if answers:  # the user's answer to one of its questions (request_evidence), this run's only
            request = next((r for r in session.requests if r.get("id") == answers and r.get("status") == "asked"
                            and self._asked_in_this_run(session, r)), None)
            if request is None:
                raise ResearchRefused(f"{session.name} has no open question {answers}",
                                      next="pass it as a plain note instead")
        said = text  # the user's own words: the request's answer
        if request is not None:  # the researcher is told which of its questions this answers
            text = f"Your question {request['id']} ({request['what']}): {text}"
        marks = {k: v for k, v in (about or {}).items() if k in ("holding", "held") and isinstance(v, str)}
        note = {"at": round(time.time(), 2), "text": text, **({"answers": request["id"]} if request is not None else {}),
                **marks}
        directory = self.root / session.day / session.rid
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / INBOX).open("a") as f:
            f.write(json.dumps(note) + "\n")
        session.notes.append(note)
        if session.status not in ACTIVE and session.mission.notes_from is None:
            session.mission.notes_from = len(session.notes) - 1
        if request is not None:
            request.update(status="answered", answer=said, answeredAt=note["at"])
        self._save(session)
        self._changed(session)
        return session

    # --- missions (P2c) -----------------------------------------------------------------------------------------------
    def _objective(self):
        reading = self.hub.scale_reading() if hasattr(self.hub, "scale_reading") else None
        return getattr(reading, "objective", None) if reading is not None else None

    def _field_facts(self) -> dict:
        from ..population import view_facts

        info = getattr(self.hub, "latest_info", None)
        return view_facts(info) if info is not None else {}

    def start_mission(self, rid: str, operation: str, every_s: float, window_s: float,
                      checkpoint_every: int = 3, *, acquisition: dict | None = None,
                      origin: dict | None = None) -> Mission:
        """The app's side of a mission (P2c-1): the field pinned now, the plan checked, its slots started. Starting
        one from a request (ADMIT, the day's money, checkpoints) is P2c-2's."""
        from .hands import OPERATIONS

        session = self.sessions.get(rid)
        if session is None or session.purpose != "research":
            raise ResearchRefused("there is no such researcher", next="say which researcher, or look in the Research tab")
        if session.mission is not None and session.mission.status in ("watching", "checkpoint", "finishing"):
            raise ResearchRefused(f"{session.name} already has a mission running", next="cancel it first, or let it end")
        # Checkpoints are model runs at its effort (deep's 18 min is no checkpoint); a capture mission has none, only its
        # analysis at the end, so a deep researcher may schedule clips.
        if session.effort not in MISSION_EFFORTS and not (acquisition is not None and session.effort == "deep"):
            raise ResearchRefused(f"a mission's researcher works at {' or '.join(MISSION_EFFORTS)} effort (its hands need "
                                  f"quick; a checkpoint is no {session.effort} run)", next="start it at quick effort")
        if session.mission is not None and session.mission.status == "interrupted":
            # Nothing carries an interrupted mission on yet (P2c-2): it is cancelled, never promised otherwise.
            raise ResearchRefused(f"{session.name}'s mission was interrupted by the app stopping, with its ledger",
                                  next="cancel it first")
        source = getattr(self.hub, "source", None)
        if source is None or not source.is_fresh():
            from ..frames import no_picture
            why, next_step = no_picture(self.hub, "there is no fresh picture from the camera to pin the field to",
                                         "check the camera is live, then ask again")
            raise ResearchRefused(why, next=next_step)
        pinned = self._field_facts()
        if pinned:
            pinned["objective"] = self._objective()
        basis = copy.deepcopy(session.admission or {"words": "", "askedWith": "", "mode": self._admit_mode(),
                                                    "effort": session.effort, "deliverable": session.deliverable})
        if (basis.get("effort"), basis.get("deliverable")) != (session.effort, session.deliverable):
            raise ResearchRefused("the researcher's retained admission does not authorize this effort and deliverable",
                                  next="ask for the mission in your own words")
        if (not isinstance(basis.get("words"), str) or not isinstance(basis.get("askedWith"), str)
                or basis.get("mode") not in ("quote", "keywords")):
            raise ResearchRefused("the researcher's retained admission cannot be read",
                                  next="ask for the mission in your own words")
        # Explicit formats without retained validated words are refused before slots or money are reserved.
        admit("continue", words=basis.get("words", ""), asked_with=basis.get("askedWith", ""),
              effort=session.effort, deliverable=session.deliverable, mode=basis.get("mode", self._admit_mode()))
        try:
            mission = mission_plan(rid, operation, every_s, window_s, pinned, self.missions.clock(),
                                   (*OPERATIONS, "capture"), checkpoint_every, acquisition=acquisition)
        except ValueError as exc:
            raise ResearchRefused(str(exc), next="say it another way: how often, for how long, and what to run") from None
        cap = self.hub.settings.research_cap
        mission.authority = {**basis, "missionId": mission.id, "rid": session.rid}
        runs = self.mission_runs(mission)
        self.spending.check(cap, runs * EFFORTS[session.effort].cap_usd)  # its whole cost, up front (ADR 0004)
        self._mission_money(mission, runs)
        previous = session.mission
        if session.mission is not None:  # an ended or cancelled one: kept with its ledger, never replaced
            session.past_missions.append(session.mission)
        mission.origin = dict(origin if origin is not None else session.origin or {})
        session.mission = mission
        try:
            self._save(session)
            self.missions.start(mission)
            self._changed(session)
        except Exception:
            self.spending.release(mission_key(rid))
            session.mission = previous
            if previous is not None:
                session.past_missions.pop()
            raise
        return mission

    def _mission_field(self, mission: Mission) -> str:
        """"" when the view now is its pinned field; else why not. While another session is open its slots are not
        comparable: the view is that session's (ADR 0017), and the mission goes on when its own is opened again."""
        session = self.sessions.get(mission.rid)
        if session is not None and session.sitting != self.session():
            return f"another session was open (its own is “{self.session_name(session.sitting)}”)"
        return field_now(mission.pinned, self._field_facts(), self._objective())

    def _mission_ends(self, mission: Mission) -> str:
        """Nothing ends a mission early but a cancel (or the app stopping: it comes back "interrupted"): midnight does
        nothing (ADR 0017: a session spans days), and while another session is open its slots are "not comparable"
        (`_mission_field`), so it goes on when its session is opened again. Checked every second while it waits."""
        return ""

    def _file_made(self, kind: str, pid: str, session: Session) -> None:
        """A product a researcher's mission made (a camera clip), journaled in the researcher's own session, so the
        session's list counts what Made in this session lists (day/catalogue.py)."""
        store = self.store
        if store is None:
            return
        try:
            try:
                target = store.elsewhere(session.sitting)
            except KeyError:
                target = store  # its session can't be found: filed here, never lost
            target.made(kind, pid, day=session.day)
        except Exception:  # the clip is kept whatever its journal line does
            log.exception("research product not journaled")

    async def _mission_slot(self, mission: Mission, slot) -> dict:
        session = self.sessions.get(mission.rid)
        if session is None:
            return {"ok": False, "error": "its researcher is no longer kept"}
        if mission.acquisition:
            from .acquisition import acquire

            def owner_check():
                if session.mission is not mission or mission.status != "watching":
                    raise ResearchRefused("the original acquisition mission is no longer running", next="start a new acquisition mission")
                why = self._mission_ends(mission)
                if why:
                    raise ResearchRefused(why, next="check the camera and specimen, then start a new acquisition mission")

            def check():
                owner_check()
                why = self._mission_field(mission)
                if why:
                    raise ResearchRefused(why, next="check the camera and specimen, then start a new acquisition mission")

            def retained():
                if (self.sessions.get(session.rid) is session
                        and (session.mission is mission or any(m is mission for m in session.past_missions))):
                    self._save(session)
                    self._changed(session)

            try:
                reply = await acquire(self.hub, self.root / "acquisitions", mission, slot, session.day, check,
                                      owner_check, retained, session=session.sitting)
                if reply.get("ok") and reply.get("id"):
                    self._file_made("clip", reply["id"], session)
                return reply
            except Exception as exc:
                # A failed interval is one failed slot; future planned slots remain unchanged.
                return {"ok": False, "error": str(exc)[:240]}
        if self.hands.busy is not None:
            return {"busy": True}  # another research operation holds the app's one slot: asked again within the grace
        reply = await self.hands.run(session, mission.operation, f"mission slot {slot.n}", scheduled=True)
        view = reply.pop("view", None) if reply.get("ok") else None
        if isinstance(view, dict):
            # The view the operation ran on (its own newest frame), checked against the pin as the view before was.
            off = field_now(mission.pinned, view, self._objective())
            if off:
                reply["off_field"] = f"the view it ran on: {off}"
        return reply

    def _mission_saved(self, mission: Mission, slot) -> None:
        session = self.sessions.get(mission.rid)
        if session is not None and session.mission is not mission:
            if any(previous is mission for previous in session.past_missions):
                # A cancelled task may settle after its replacement was saved.
                # Retain the old attempt without adopting it or releasing new money.
                self._save(session)
                self._changed(session)
            return  # a late callback cannot adopt an earlier mission or release the replacement's money
        if mission.status in ("done", "cancelled", "failed"):
            self.spending.release(mission_key(mission.rid))  # what its model runs did not use is free again
        if session is None:
            return
        session.mission = mission
        self._save(session)
        self._changed(session)
        failure = (mission.rid, mission.id)
        if mission.status == "failed" and failure not in self._notified_mission_failures:
            self._notified_mission_failures.add(failure)
            for hook in self.on_mission_failed:
                try:
                    hook(session, mission, dict(mission.origin))
                except Exception:
                    log.exception("mission failure listener failed")

    # --- a mission's model runs (P2c-2): checkpoints and the finding --------------------------------------------------
    def mission_runs(self, mission: Mission) -> int:
        """How many model runs a mission makes at most: a checkpoint every `checkpoint_every` done slots (none after
        the last) and its finding at the end."""
        return 1 if mission.acquisition else max(0, (mission.slots - 1) // max(1, mission.checkpoint_every)) + 1

    def _mission_money(self, mission: Mission, runs_left: int) -> None:
        """What its model runs still to come may cost, reserved in the day's ledger beside the run going now (which
        holds its own): the day never goes over its cap (ADR 0004)."""
        session = self.sessions.get(mission.rid)
        cap = EFFORTS[session.effort].cap_usd if session is not None else 0.0
        self.spending.reserve(mission_key(mission.rid), max(0, runs_left) * cap)

    async def _until_idle(self, rid: str) -> None:
        session = self.sessions.get(rid)
        while session is not None and session.status in ACTIVE:
            await self.missions.sleep(1.0)

    async def _mission_run(self, mission: Mission, question: str, runs_left: int) -> bool:
        """One of its model runs: a "continue" of the researcher, once whatever it is doing now has ended, with the
        ledger as the app's facts (entries by id, read with its session tool); never two at once."""
        await self._until_idle(mission.rid)
        session = self.sessions.get(mission.rid)
        if session is None:
            raise RuntimeError("its researcher is no longer kept")
        if session.mission is not mission or mission.status == "cancelled":
            raise asyncio.CancelledError
        if why := self._mission_ends(mission):
            mission.status, mission.why_ended = "cancelled", why
            self._mission_saved(mission, None)
            return False
        self._mission_money(mission, runs_left - 1)
        try:
            authority = mission.authority
            if (authority.get("rid"), authority.get("missionId"), authority.get("effort"), authority.get("deliverable")) != (
                    session.rid, mission.id, session.effort, session.deliverable):
                raise ResearchRefused("this mission has no retained admission for this researcher, effort and deliverable",
                                      next="ask for a new mission")
            admitted = admit("continue", words=authority.get("words", ""), asked_with=authority.get("askedWith", ""),
                             effort=session.effort, deliverable=session.deliverable,
                             mode=authority.get("mode", self._admit_mode()))
            self.spending.check(self.hub.settings.research_cap, EFFORTS[admitted.effort].cap_usd)
            self._continue(session, question, mission.origin, mission)
            attempt = session._attempt
        except ResearchRefused as exc:  # the day's money, or it can't carry on: said in the ledger's words
            self._log(session, {"kind": "mission-run", "ok": False, "error": str(exc)[:200]})
            raise
        outcome = await asyncio.shield(attempt.completion)
        if mission.status in ("failed", "done"):
            return False  # retire this waiter; a late receipt cannot rewrite an ended occurrence
        if session.mission is not mission or mission.status == "cancelled":
            raise asyncio.CancelledError
        if outcome["status"] != "done" or not outcome["result"]:
            raise RuntimeError(f"its researcher ended {outcome['status']} ({outcome['cause']}) without a finding")
        return True

    async def _mission_checkpoint(self, mission: Mission) -> None:
        runs_done = sum(1 for r in mission.model_runs if r.get("dispatchedAt") is not None)
        nxt = mission.next_due()
        await self._mission_run(mission, mission_words(mission, final=False, next_at=nxt),
                                self.mission_runs(mission) - runs_done)

    async def _mission_finish(self, mission: Mission) -> None:
        await self._mission_run(mission, mission_words(mission, final=True), 1)

    def close_question(self, rid: str, answers: str, text: str) -> Session:
        """The user's late answer that asks for nothing more ("no" to footage): filed on its question, no run started
        (#194 review SF3: a "no" never costs a paid run)."""
        session = self.sessions.get(rid)
        if session is not None:
            self._this_session_only(session)
        request = self.open_question(session, answers) if session is not None else None
        if request is None:
            raise ResearchRefused(f"{session.name if session else 'that researcher'} has no open question {answers}",
                                  next="pass it as a plain note instead")
        request.update(status="answered", answer=_whole(" ".join(str(text or "").split()), "the answer"),
                       answeredAt=round(time.time(), 2), late=True)
        self._save(session)
        self._changed(session)
        return session

    def open_question(self, session: Session, answers: str | None) -> dict | None:
        """An unanswered question from the latest run, or an earlier checkpoint of this exact mission.

        A saved legacy question with no mission occurrence stays restricted to its latest run."""
        return next((r for r in session.requests if r.get("id") == answers and r.get("status") in ("asked", "unanswered")
                     and (self._asked_by_latest_work(session, r)
                          or (session.mission is not None and r.get("missionId") == session.mission.id))), None) if answers else None

    @staticmethod
    def _asked_in_this_run(session: Session, request: dict) -> bool:
        """A question this run asked: by the run's own id, which nothing renumbers (a question saved before run ids
        were kept falls back to the run count it was filed with)."""
        attempt = session._attempt
        if request.get("runId") and attempt is not None:
            return request["runId"] == attempt.id
        return request.get("run", 0) == len(session.history)

    @staticmethod
    def _asked_by_latest_work(session: Session, request: dict) -> bool:
        """A question asked by the run whose work is the researcher's current result, by that run's id: a follow-up
        answer or a new version filed since (each adds a history entry) does not make it stale."""
        made = (session.result_run or {}).get("runId")
        if request.get("runId") and made:
            return request["runId"] == made
        return request.get("run", 0) == len(session.history)

    PROPOSAL_S = 180.0  # how long a proposal waits for the user's yes

    def propose_start(self, *, brief: str, effort: str | None, deliverable: str | None, thread: str | None,
                      object_id: str | None, why: str) -> str:
        """A big deliverable the user did not ask for in their own words, kept for their yes: its id (p1...)."""
        pid = f"p{next(self._proposal_ids)}"
        self.proposals[pid] = {"id": pid, "brief": brief, "effort": effort, "deliverable": deliverable, "thread": thread,
                               "object_id": object_id, "why": why, "at": time.time()}
        return pid

    def take_proposal(self, pid: str, thread: str | None) -> dict:
        """The proposal the user's yes confirms, once: only in the conversation it was made in, and only while fresh."""
        proposal = self.proposals.get(str(pid or ""))
        if proposal is None or time.time() - proposal["at"] > self.PROPOSAL_S or proposal["thread"] != thread:
            raise ResearchRefused(f"there is no open proposal {pid}", next="propose it again and ask the user")
        return self.proposals.pop(proposal["id"])

    def _this_session_only(self, session: Session) -> None:
        """Only this session's work is continued (ADR 0017): a researcher of another session gets no run, note or
        answer from this one (its work and what it spends are filed there). One of this session started on an earlier
        date is continued as any other."""
        if session.sitting != self.session():
            raise ResearchRefused(f"{session.name} belongs to “{self.session_name(session.sitting)}”; switch to it to "
                                  "continue", next=None)  # no next: the message says what to do

    async def carry_on(self, rid: str, answers: str, text: str, *, origin: dict | None = None, words: str = "",
                       asked_with: str = "", delivery: Delivery | None = None) -> Session:
        """The user's answer to a question a researcher asked, come after it finished (the end of the effort's kind
        (h): "a researcher asked 'yes or no' and finished before the yes"): never dropped. It carries on with the
        answer (a "continue" run at its own effort, the answer being the user's request: ADMIT as for any continue,
        so a lesson, a video or a deep run still needs the user's own words), and the question is marked answered,
        late. Refused, saying why, when it has no such open question or may not carry on."""
        session = self.sessions.get(rid)
        if session is None or session.purpose != "research":
            raise ResearchRefused("there is no such researcher", next="say which researcher, or look in the Research tab")
        self._this_session_only(session)
        if session.status == "cancelled":  # the user stopped it: a later yes never starts paid work again (#194 SF2)
            raise ResearchRefused(f"{session.name} was stopped, so it doesn't carry on", next="ask for a new researcher")
        request = self.open_question(session, answers)
        if request is None:
            raise ResearchRefused(f"{session.name} has no open question {answers}", next="ask it a follow-up instead")
        stopped = next((m for m in [session.mission, *session.past_missions]
                        if m is not None and m.id == request.get("missionId") and m.status == "cancelled"), None)
        if stopped is not None:
            raise ResearchRefused(f"{session.name}'s mission was stopped, so it doesn't carry on",
                                  next="ask for a new researcher")
        said = _whole(" ".join(str(text or "").split()), "the answer")
        question = (f"The user answered your question {request['id']} ({request['what']}) after you had finished: "
                    f'"{said}". Carry on from where you left off with their answer.')
        # Held to ADMIT like any continue (#194 review): a lesson, a video or a deep run made again needs the user's own
        # words for it, not a bare "yes"; research carries on on their answer.
        await self.followup(rid, question, origin=origin, mode="continue", words=words, asked_with=asked_with,
                            delivery=delivery)
        request.update(status="answered", answer=said, answeredAt=round(time.time(), 2), late=True)
        self._save(session)
        self._changed(session)
        return session

    def by_number(self, n: int) -> Session | None:
        """Researcher n of this session (only researchers have numbers: never the brain's corrections or a
        description; each session counts its own from 1)."""
        return next((s for s in self.researchers() if s.n == n), None)

    # --- starting ---------------------------------------------------------------------
    async def start(self, brief: str, *, effort: str = "quick", deliverable: str | None = None, object_id: str | None = None,
                    origin: dict | None = None, purpose: str = "research", label: str = "", fake: dict | None = None,
                    words: str = "", asked_with: str = "", delivery: Delivery | None = None,
                    dependencies: list[dict] | None = None, confirmed: bool = False,
                    expected_session: str | None = None) -> Session:
        """Queue a researcher and return at once (its snapshot is taken now). `words`: what the
        user said in the last half minute, for admission (policy.admit: big deliverables need an
        explicit request; an incompatible format is refused without increasing effort). A scripted researcher (`fake`, the
        scenario harness) is test plumbing and skips it. The day's cap is checked."""
        from ..ticket import current
        if expected_session is not None and expected_session != self.session():
            raise ResearchRefused('The original Project changed before this researcher was admitted.',
                                  next='return to the original Project and start a new request')
        request = current()
        if getattr(request, 'workspace_origin', None):
            origin = {**(origin or {}), 'workspace':copy.deepcopy(request.workspace_origin)}
        if getattr(self.store, "local_only", False):
            raise ResearchRefused("This example session runs locally. Open another session to use AI researchers.",
                                  next=None)  # no next: its words say what to do (open another session)
        if fake is None:
            # Its model's key first: nothing is proposed, held or queued that could only fail at its first round. A worker
            # channel that runs no model (the acting bench's stand-in says runs_a_model = False) needs no key.
            missing = engine_key_refusal(getattr(self.hub.settings, "research", None)) \
                if getattr(self._worker, "runs_a_model", True) else None
            if missing is not None:
                raise missing
            admitted = admit("describe" if purpose == "description" else "start", words=words, effort=effort,
                             deliverable=deliverable, asked_with=asked_with, mode=self._admit_mode(), confirmed=confirmed)
            effort, deliverable = admitted.effort, admitted.deliverable
            origin = {"asked": admitted.asked, **(origin or {})}
        elif effort not in EFFORTS or effort == REVISE_EFFORT:
            raise ResearchRefused(f"unknown effort {effort!r}", next=None)  # no next: the model's own call; it corrects it
        self.spending.check(self.hub.settings.research_cap, EFFORTS[effort].cap_usd)
        bound_inputs = []
        for ref in dependencies or []:
            if ref.get('kind') == 'research-work':
                # A finished researcher's work as it is kept (today's or a restored one: no attempt to wait on),
                # named by its finding's version so later revisions are different work.
                source = self.sessions.get(ref.get('researcherId'))
                if source is None or source.purpose != 'research' or source.status not in KEPT or not source.result:
                    raise ResearchRefused("that researcher has no finished work to build on", next='say which finished researcher')
                bound_inputs.append({'reference': copy.deepcopy(ref), 'result': copy.deepcopy(source.result),
                                     'outputs': copy.deepcopy(source.current_outputs())})
                continue
            original = self.result_view(ref)
            bound_inputs.append({'reference': copy.deepcopy(ref), 'result': copy.deepcopy(original.result),
                                 'outputs': copy.deepcopy(original.outputs)})
        if len(bound_inputs) > 6 or len(json.dumps(bound_inputs)) > 240000:
            raise ResearchRefused('the exact handoff inputs exceed this researcher’s context bound', next='choose fewer completed inputs for this handoff')
        obj = self.hub.scene.objects.get(object_id) if object_id else None
        # The session the request was asked in (its Asked), else the one open: it belongs there for good (ADR 0017).
        asked_in = getattr(getattr(delivery, "asked", None), "session", None)
        sitting = asked_in if sids.is_sid(asked_in) else self.session()
        brief = _whole(brief.strip(), "the request")
        session = Session(
            rid=f"r{int(time.time()) % 100000:05d}{next(self._ids)}", n=0 if purpose == "description" else self._next_number(sitting),
            brief=brief, deliverable=deliverable, effort=effort,
            engine="fake" if fake is not None else getattr(self.hub.settings, "research", "gpt-6.1-sol"), purpose=purpose,
            label=label or (obj.label if obj else ""), object_id=obj.id if obj else None, origin=origin or {},
            deliver="tab" if purpose == "research" and (origin or {}).get("workspace") else None,
            day=self.today(), fake=fake, sitting=sitting, specimen=str(self.hub.scene.context.get("specimen") or "")[:80],
            root=self.root, revision_now=self._revision_now, dependencies=bound_inputs)
        if fake is None:
            session.admission = {"words": words, "askedWith": asked_with, "mode": self._admit_mode(),
                                 "effort": effort, "deliverable": deliverable}
        session._delivery = delivery  # fixed before snapshot I/O or worker dispatch
        self._new_attempt(session)
        directory = self.root / session.day / session.rid
        # Held before the first await: two starts in one turn must not both pass the check.
        self.spending.hold(session, EFFORTS[effort].cap_usd)
        try:
            if bound_inputs or (origin or {}).get('study'):
                # An explicit retained-work handoff never silently substitutes the camera now.
                (directory / 'inputs').mkdir(parents=True, exist_ok=True)
                snapshot = {'learner': learner_of(self.hub.settings)}
            else:
                snapshot = await self._take_snapshot(directory, obj)
        except BaseException:
            self.spending.release(session.rid)
            raise
        session.has_crop = bool(snapshot.get("crop"))
        self.sessions[session.rid] = session
        self._snapshots[session.rid] = snapshot
        try:
            self._save(session)
        except OSError:
            self.sessions.pop(session.rid, None)
            self._snapshots.pop(session.rid, None)
            self.spending.release(session.rid)
            raise
        self.queue.append(session.rid)
        self._changed(session)
        self._pump()
        return session

    def _snapshot_context(self) -> tuple[dict, Any]:
        """Capture the request's words, preferences and frame before asynchronous snapshot I/O."""
        info = self.hub.latest_info
        return ({"specimen": self.hub.scene.context.get("specimen"),
                 "turns": self.conversation.text() if self.conversation is not None else "",
                 "frame_id": info.id if info else None,
                 "learner": learner_of(self.hub.settings), **_door()}, info)

    async def _take_snapshot(self, directory: Path, obj) -> dict:
        from ..capture import CaptureGone, Refused, captures

        meta, info = self._snapshot_context()
        store = captures(self.hub)
        with ExitStack() as holds:
            frame = None
            try:
                if info is not None:
                    hold = holds.enter_context(store.hold(store.take(info.id), "analysis", "bytes", "a researcher's snapshot"))
                    frame = hold.capture
            except (CaptureGone, Refused):
                pass  # no photo: the researcher works from the original words
            crop_hub = None
            if obj is not None:
                # Freeze placement and wording without copying the object's heavy templates or masks.
                obj = copy.copy(obj)
                obj.polygons = copy.deepcopy(obj.polygons)
                # The same bounded candidates crop_jpeg considers, retained before I/O can outlive the ring.
                candidates = [view for view in list(getattr(self.hub, "infos", {}).values())
                              if info is not None and view.epoch == obj.anchor_epoch and info.wall_at - view.wall_at <= 2.0]
                candidates = [view for view in candidates if view.steady] or candidates
                candidates.sort(key=lambda view: view.focus, reverse=True)
                views = {}
                for view in candidates[:8]:
                    try:
                        capture = store.take(view.id)
                        if capture is not frame:
                            holds.enter_context(store.hold(capture, "analysis", "bytes", "a researcher's original crop"))
                        views[view.id] = capture
                    except (CaptureGone, Refused):
                        continue
                crop_hub = SimpleNamespace(latest_info=copy.copy(info), infos={fid: view.info for fid, view in views.items()},
                                           source=SimpleNamespace(get=views.get), captures=store)
            context = (meta, frame, crop_hub)
            write = asyncio.get_running_loop().run_in_executor(None, self._write_snapshot, directory, obj, context)
            # Cancellation stops admission, not the executor's I/O. Keep its bytes counted until it actually exits.
            retained = holds.pop_all()
            write.add_done_callback(lambda _done: retained.close())
            return await asyncio.shield(write)

    def _write_snapshot(self, directory: Path, obj, context: tuple[dict, Any, Any] | None = None) -> dict:
        """inputs/frame.jpg (the eyepiece view) and inputs/crop.jpg (the object's sharpest
        frame), plus what the app knows."""
        if context is None:
            meta, info = self._snapshot_context()
            crop_hub = self.hub
        else:
            meta, info, crop_hub = context
        (directory / "inputs").mkdir(parents=True, exist_ok=True)
        hub = self.hub
        from ..capture import CaptureGone, Refused, decoding

        try:
            if info is not None:
                with decoding(hub, info, "look", owner="a researcher's photo") as held:  # counted (invariant 3)
                    image = held.bgr()
                    if image is not None:
                        jpeg, _ = encode_crop(image, fov_crop(info.fov, image.shape[1], image.shape[0]), max_side=1024)
                        (directory / "inputs" / "frame.jpg").write_bytes(jpeg)
                        meta["frame"] = "inputs/frame.jpg"
        except (CaptureGone, Refused):
            pass  # no photo: the researcher works from the words
        if obj is not None:
            meta["object"] = {"id": obj.id, "label": obj.label, "confidence": obj.confidence, "note": obj.seen or obj.note}
            found = crop_jpeg(crop_hub, obj, pad=0.3)
            if found is not None:
                (directory / "inputs" / "crop.jpg").write_bytes(found[0])
                meta["crop"] = "inputs/crop.jpg"
        return meta

    # --- scheduling -------------------------------------------------------------------
    def _free(self, session: Session) -> bool:
        pool = EFFORTS[session.run_effort].pool
        running = self.running(pool)
        if len(running) >= POOLS[pool]:
            return False
        if session.purpose == "description":
            return sum(s.purpose == "description" for s in running) < DESCRIPTION_SLOTS
        return True

    def _pump(self) -> None:
        for rid in list(self.queue):
            session = self.sessions[rid]
            if session.status != "queued":
                self.queue.remove(rid)
            elif self._free(session):
                self.queue.remove(rid)
                session.status = "starting"
                asyncio.ensure_future(self._launch(session))

    async def _ensure_worker(self) -> ProcessChannel:
        if self._worker is not None and self._worker.alive:
            return self._worker
        if self._starting is None or self._starting.done():
            self._starting = asyncio.create_task(self._start_worker())
        await asyncio.shield(self._starting)
        return self._worker

    async def _start_worker(self) -> None:
        if self._restarts:
            await asyncio.sleep(min(30.0, 2.0 ** self._restarts))
        from research_worker.worker import serve

        channel = ProcessChannel(self._worker_target or serve, "micromind-research", self._event, self._request, self._worker_exited)
        await channel.start()
        self._worker = channel

    def _worker_exited(self) -> None:
        self._restarts += 1
        for session in self.sessions.values():
            if session.status in ("starting", "running"):
                self._finish(session, "interrupted", None)

    async def _launch(self, session: Session) -> None:
        if getattr(self, "_limit_reconciling", False):
            self._finish(session, "cancelled", None)
            return
        attempt = session._attempt or self._new_attempt(session)
        try:
            worker = await self._ensure_worker()
            if session._attempt is not attempt or attempt.completion.done():
                return  # a canceled/replaced queued attempt is never dispatched by an old launch task
            snapshot = self._snapshots.pop(session.rid, {})
            # What it had spent and made before this run: the worker reports this run only.
            session._before = {"spent": session.spent, "estimated": session.spent_estimated, "outputs": len(session.outputs),
                           "findings": len(session.findings)}
            job = (session.run.worker_job() if session.run is not None
                   else {"brief": session.package or session.brief, "deliverable": session.deliverable, "revise": None})
            attempt.record.update(status="dispatched", dispatchedAt=time.time())
            self._save(session)
            reply = await worker.call("spawn", rid=session.rid, run_id=attempt.id, n=session.n, brief=job["brief"], deliverable=job["deliverable"],
                              effort=session.run_effort, engine=session.engine, label=session.label, purpose=session.purpose,
                              dir=str(self.root / session.day / session.rid), snapshot=snapshot,
                              dependencies=copy.deepcopy(session.dependencies),
                              image_model=getattr(self.hub.settings, "image", None), fake=session.fake,
                              lesson_check=getattr(self.hub.settings, "lesson_check", "off"),
                              pacing=getattr(self.hub.settings, "research_pacing", "forced"),
                              claim_check=getattr(self.hub.settings, "claim_check", "model"),
                              independent_presentation=getattr(self.hub.settings, 'presentation', 'off') != 'off',
                              previous_response_id=None,  # every run starts from what it is given (Phase 3)
                              revise=job["revise"], inbox_from=session._notes_from,
                              requests_from=len(session.requests),  # its questions numbered on from earlier runs' (one numbering)
                              resources_url=self.resources_url, day=session.day, session=session.sitting,
                              transcript=(str(self.transcripts / session.day / f"{session.rid}.jsonl")
                                          if self.transcripts is not None else None),
                              # A monthly cap bounds each run to its effort's most, the amount its hold reserved
                              # against the month (ledger.hold), so runs never share one remaining allowance.
                              unlimited_budget=(not session.origin.get('study') and session.run_effort != "build" and self.hub.settings.research_cap is None
                                                and not _month_capped()))
            if isinstance(reply, dict) and reply.get('ok') is False:
                raise RuntimeError(reply.get('error') or 'worker refused dispatch')
        except Exception as exc:  # noqa: BLE001 - whatever stops it starting must free its slot
            if session._attempt is attempt:
                session.step = "It could not start; ask again to try once more"
                self.log({"kind": "research-start-failed", "rid": session.rid, "error": f"{type(exc).__name__}: {exc}"[:300]})
                self._finish(session, "interrupted", None)
            return
        if session._attempt is not attempt or attempt.completion.done() or session.status not in ACTIVE:
            return
        session.started = time.time()
        session._heard = time.monotonic()
        self._watchdogs[session.rid] = asyncio.create_task(self._watchdog(session.rid, WATCHDOG_SILENT_S))

    async def _watchdog(self, rid: str, silent: float) -> None:
        """No clock stops a researcher (research_worker/budget.py): this stops only one the app has not heard from in
        `silent` seconds (a working researcher says it is alive every ALIVE_EVERY_S, however long its work takes), so
        a stuck or gone research process never holds its slot or its money forever."""
        session = self.sessions.get(rid)
        attempt = session._attempt if session is not None else None
        while True:
            await asyncio.sleep(min(15.0, silent))
            session = self.sessions.get(rid)
            if (session is None or session._attempt is not attempt or session.status not in ("starting", "running")
                    or time.monotonic() - session._heard >= silent):
                break
        session = self.sessions.get(rid)
        if session is not None and session._attempt is attempt and session.status in ("starting", "running"):
            if attempt is not None:
                attempt.forced_status = "timeout"
                attempt.terminating_task = asyncio.current_task()
            try:
                if self._worker is not None:
                    await self._worker.call("cancel", rid=rid)  # stop this worker, not the user's mission
                else:
                    raise WorkerGone("worker not running")
                if attempt is not None:
                    # The RPC only acknowledges a cancellation request. Keep the run's hold until its own
                    # terminal event arrives; that event supplies final usage and any partial artifact.
                    # A missing receipt still ends the mission finitely, without ending the live worker
                    # or resolving its completion future. Use the scheduler's existing monotonic clock.
                    deadline = self.missions.mono() + TERMINAL_RECEIPT_GRACE_S
                    while not attempt.completion.done():
                        remaining = deadline - self.missions.mono()
                        if remaining <= 0:
                            raise TimeoutError("worker termination not confirmed")
                        await self.missions.sleep(min(1.0, remaining))
                    await asyncio.shield(attempt.completion)
            except WorkerGone:
                if session._attempt is attempt:
                    self._finish(session, "timeout", None)
            except (RuntimeError, TimeoutError):
                if session._attempt is attempt and session.status in ACTIVE:
                    session.step = "not heard from in a while; waiting for the research process to stop"
                    if attempt is not None and attempt.mission is not None and session.mission is attempt.mission:
                        mission = attempt.mission
                        if mission.status not in ("done", "cancelled", "failed"):
                            mission.status, mission.why_ended = "failed", f"{attempt.phase}: timeout; worker termination not confirmed"
                            self._mission_saved(mission, None)
                    else:
                        self._save(session)
                        self._changed(session)

    async def reconcile_limit(self) -> int:
        """Stop the old allowance before confirming a tighter limit. New dispatches wait for a fresh request."""
        self._limit_reconciling = True
        affected = [s for s in self.sessions.values() if s.status in ACTIVE or
                    (s.mission is not None and s.mission.status not in ("done", "cancelled", "failed"))]
        try:
            for session in affected:
                await self.cancel(session.rid, confirmed=True)
        except (WorkerGone, RuntimeError, TimeoutError):
            # A silent child cannot keep its old allowance. stop() joins, terminates, then joins again.
            worker = self._worker
            if worker is not None:
                await asyncio.to_thread(worker.stop)
                self._worker = None
            for session in affected:
                if session.status in ACTIVE:
                    self._finish(session, "cancelled", None)
        finally:
            self._limit_reconciling = False
        return len(affected)

    async def cancel(self, rid: str, *, confirmed: bool = False) -> bool:
        session = self.sessions.get(rid)
        if session is None:
            return False
        # The model may have finished between scheduled slots. Stopping the researcher still
        # stops its mission, including an interrupted one, without discarding completed work.
        mission = session.mission
        stopped_mission = mission is not None and mission.status not in ("done", "cancelled", "failed")
        if stopped_mission:
            self.missions.cancel(rid, mission=mission)
            task = self.missions.tasks.get(rid)
            if task is not None and task is not asyncio.current_task():
                await asyncio.gather(task, return_exceptions=True)
        if session.status not in ACTIVE:
            return stopped_mission
        if session.status == "queued":
            self._finish(session, "cancelled", None)
            return True
        try:
            if self._worker is not None:
                reply = await self._worker.call("cancel", rid=rid, **({"wait": True} if confirmed else {}))
                if confirmed and not reply.get("terminated"):
                    raise RuntimeError("worker termination not confirmed")
            elif confirmed:
                self._finish(session, "cancelled", None)
        except (WorkerGone, RuntimeError, TimeoutError):
            if confirmed:
                raise
            self._finish(session, "cancelled", None)
        return True

    # --- from the worker ----------------------------------------------------------------
    def _event(self, message: dict) -> None:
        session = self.sessions.get(message.get("rid"))
        if session is None:
            return
        run_id = message.get('runId')
        current_run = session._attempt.id if session._attempt is not None else None
        if run_id and message.get('event') in ('cost', 'done'):
            if run_id not in session.usage_runs and run_id != current_run:
                return  # no unrelated worker can attribute a charge or outcome to this session
            usage = session.usage_runs.setdefault(run_id, {'spent': 0.0, 'estimated': 0.0, 'sequence': -1})
            sequence = message.get('eventSequence', usage['sequence'] + 1)
            if sequence > usage['sequence']:
                spent = max(usage['spent'], float(message.get('spent') or 0))
                estimated = max(0.0, float(message.get('estimated', message.get('spent_estimated')) or 0))
                session.spent += spent - usage['spent']
                session.spent_estimated += estimated - usage['estimated']
                usage.update(spent=spent, estimated=estimated, sequence=sequence)
                if message.get('event') == 'done':
                    usage['outcomeUnknown'] = bool(estimated)
                self._save(session)
                self._changed(session)
        # Billing can arrive after a timeout. Keep the charge without reopening the work.
        charge = message.get("charge") if message.get("event") == "cost" else None
        if charge and session.fake is None and not str(charge.get("model", "")).startswith("fake"):
            from research_worker import api_usage

            run_day = (session.usage_runs.get(run_id) or {}).get("day") if run_id else None
            api_usage.record(charge["model"], charge.get("usd"), area="Research",
                             day=run_day or session.day, basis=charge.get("basis", "Research estimate"),
                             key=f"research:{session.day}:{session.rid}:{run_id}:{message['receiptId']}" if message.get('receiptId') else None)
        if run_id and run_id != current_run:
            return  # a receipt can reconcile the old job; none of its screen/content effects can revive
        if session._attempt is not None and (session._attempt.completion.done()
                                             or session._attempt.record.get("dispatchedAt") is None):
            return  # no worker message can reopen a terminal attempt or complete one not dispatched yet
        kind = message["event"]
        session._heard = time.monotonic()  # the watchdog stops only a researcher it stops hearing from
        if kind == "alive":
            return
        if kind == "status":
            session.status = message["status"]
        elif kind == "step":
            session.step = message["text"]
            session.step_at = time.time()
            if message.get("kind") == "stall":
                self._log(session, {"kind": "research-stall", "text": message["text"]})
        elif kind == "cost":
            if not run_id:  # compatible scripted/older producers have no attempt identity
                session.spent = session._before.get("spent", 0.0) + float(message["spent"])
                session.spent_estimated = session._before.get("estimated", 0.0) + float(message.get("estimated") or 0)
        elif kind == "output":
            if message.get("check"):
                self._log(session, {"kind": "lesson-check", "option": getattr(self.hub.settings, "lesson_check", "off"),
                                    **message["check"]})
            versions.number(session.outputs)
            # A new version replaces the current version of its line (lines never branch).
            old = versions.head(session.outputs, message.get("replaces")) if message.get("replaces") else None
            session.outputs.append({k: message[k] for k in ("kind", "path", "caption", "fidelity", "content", "check") if k in message}
                                   | {"generated": bool(message.get("generated")), "altered": bool(message.get("altered")),
                                      "id": versions.next_id(session.outputs),
                                      "version": int(old.get("version") or 1) + 1 if old else 1,
                                      "replaces": old["id"] if old else None,
                                      "change": str(message.get("change") or "") or None if old else None})
            basis = (old or session.result or {}).get('findingRevision')
            if basis:
                session.outputs[-1]['findingRevision'] = copy.deepcopy(basis)
        elif kind == 'findings':
            from research_worker.results import full_report
            result = strip_citations(message.get('result') or {})
            if full_report(result.get('report')) is None:
                raise ValueError('findings event requires a full report')
            revision = scientific_findings.retain(session.finding_revisions, result, session.evidence_reads or session.working,
                                                   session._attempt.facts() if session._attempt else None,
                                                   missing=session.evidence_reads_missing)
            result['findingRevision'] = scientific_findings.reference(revision)
            session.result = result
        elif kind == "finding":
            finding = {"kind": message.get("kind"), "text": strip_citations(message.get("text")), "sources": message.get("sources") or []}
            session.findings.append(finding)
            self._ledger(session, finding)
        elif kind == "plan":
            session.plan = {"searches": [str(q) for q in message.get("searches") or []],
                            "sources": str(message.get("sources") or "")}
            self._save(session)
            self._changed(session)
            for hook in self.on_plan:
                try:
                    hook(session)
                except Exception:  # a broken listener never stops a researcher; it is logged
                    log.exception("research listener failed")
            return
        elif kind == "looked":
            # Its working set (the mission card): once per record, way, revision and run, the newest kept and the
            # rest counted. The title is the app's own index's when the research process gave none (a picture).
            from ..untrusted import inline

            eid, how = str(message.get("id") or ""), str(message.get("how") or "")
            if not ENTRY_ID.fullmatch(eid) or not LOOKED_HOW.fullmatch(how):
                return
            version = message.get("version") if isinstance(message.get("version"), int) else None
            # The run it was read in: an answer or a revision files its history entry only when it finishes, so
            # one in progress is the next number (re-check B1); "continue" filed its entry as it started.
            run = session.run_number()
            content_revision = message.get("contentRevision")
            content_revision = content_revision if isinstance(content_revision, str) and len(content_revision) <= 128 else None
            if any(w["id"] == eid and w["how"] == how and w.get("version") == version
                   and w.get("contentRevision") == content_revision and w.get("run", 0) == run
                   for w in session.evidence_reads):
                return
            title, kind_of = inline(message.get("title"), 80), inline(message.get("kind"), 30)
            if not title:
                title, kind_of = self._entry_words(session.sitting, eid, kind_of)
            read = {"id": eid, "how": how, "title": title, "kind": kind_of, "version": version,
                    "run": run, "at": round(time.time(), 2)}
            if content_revision:
                read["contentRevision"] = content_revision
            session.evidence_reads.append(read)
            session.working.append(copy.deepcopy(read))
            if len(session.working) > WORKING_MAX:
                session.working_dropped += len(session.working) - WORKING_MAX
                del session.working[:-WORKING_MAX]
            self._save(session)
            self._changed(session)
            return
        elif kind == "request":
            attempt = session._attempt
            # Numbered here, through the researcher's life (a worker numbers per run), and tied to this run.
            request = {"id": f"q{len(session.requests) + 1}", "run": session.run_number(),
                       "what": " ".join(str(message.get("what") or "").split()),
                       "why": " ".join(str(message.get("why") or "").split()),
                       "seconds": message.get("seconds") if isinstance(message.get("seconds"), int) else None,
                       "at": round(time.time(), 2), "status": "asked",
                       **({"keepGoing": True} if message.get("keepGoing") else {})}
            request.update(attempt.facts() if attempt is not None else {"origin": dict(session.origin)})
            session.requests.append(request)
            self._save(session)
            self._changed(session)
            for hook in self.on_request:
                try:
                    hook(session, request)
                except Exception:  # a broken listener never stops a researcher; it is logged
                    log.exception("research listener failed")
            return
        elif kind == "correction":
            self.propose(session, message.get("object_id"), str(message.get("to_label") or ""), str(message.get("reason") or ""),
                         message.get("confidence"), message.get("sources"))
            return
        elif kind == "done":
            if message.get("engine") and session.fake is None:
                session.engine = str(message["engine"])  # the engine that ran, never only the one the setting named
            if not run_id and message.get("spent") is not None:
                session.spent = session._before.get("spent", 0.0) + float(message["spent"])
            if not run_id and message.get("spent_estimated") is not None:
                session.spent_estimated = session._before.get("estimated", 0.0) + float(message["spent_estimated"])
            if session.run is None:  # an answer or a new version does not move the chain its work continues from
                session.response_id = message.get("response_id") or session.response_id
            self._finish(session, message["status"], message.get("result"))
            return
        self._save(session)
        self._changed(session)

    def _finish(self, session: Session, status: str, result: dict | None) -> None:
        if session.status not in ACTIVE:
            return
        attempt = session._attempt
        cause = status = attempt.forced_status if attempt is not None and attempt.forced_status else status
        did = getattr(self, "drafts", {}).get(session.rid)
        if did:
            from .. import drafts
            drafts.finish(did, cause)
        for request in session.requests:  # closed with its run: an answer that comes later carries it on (carry_on)
            if request.get("status") == "asked":
                request["status"] = "unanswered"
        self.spending.release(session.rid)  # what it did not spend is free again
        watchdog = self._watchdogs.pop(session.rid, None)
        if (watchdog is not None and watchdog is not asyncio.current_task()
                and (attempt is None or watchdog is not attempt.terminating_task)):
            watchdog.cancel()
        result = strip_citations(result) if result else None
        if session.run is None and (session.result or {}).get('report') and not (result or {}).get('report'):
            # A presentation timeout or a short final recap is not new science.
            # Preserve the full independently saved findings in the normal reader.
            result = {**copy.deepcopy(session.result), **({'lesson':copy.deepcopy(result['lesson'])} if (result or {}).get('lesson') else {})}
        receipt_status, receipt_result = status, result
        receipt_mode = None
        lesson_revision = bool(session.run is not None and session.run.kind == 'revise'
                               and session.run.kind_name == 'lesson' and (result or {}).get('lesson') and status == 'done')
        if lesson_revision:
            result = {**copy.deepcopy(session.result or {}), 'lesson':copy.deepcopy(result['lesson'])}
        # Scientific records do not inherit presentation version numbers. Retain
        # the exact evidence/result before a presentation listener can fail.
        if result and (result.get('report') or result.get('lesson')) and (session.run is None or lesson_revision):
            revision = scientific_findings.retain(session.finding_revisions, result, session.evidence_reads or session.working,
                                                   attempt.facts() if attempt is not None else None,
                                                   missing=session.evidence_reads_missing)
            result['findingRevision'] = scientific_findings.reference(revision)
        source_revision = ((session.run.target or {}).get('findingRevision')
                           if session.run is not None and session.run.kind == 'revise'
                           else (result or {}).get('findingRevision'))
        # Independent producers can finish during this run. Their job-owned outputs
        # belong only to their own completion and retain their original finding basis.
        made = [output for output in session.outputs[session._before.get('outputs', 0):] if not output.get('job')]
        for output in made:
            if 'findingRevision' not in output and source_revision:
                output['findingRevision'] = copy.deepcopy(source_revision)
        if session.run is not None:
            # A follow-up answer or a new version: recorded in its history; the work, its result
            # and its status stay as they were (run.py).
            run = session.run
            receipt_mode = run.kind
            entry = run.finish(status, result, made)
            if entry.get("status") not in ("done", "partial") and session.step:
                entry["why"] = session.step  # what stopped it, kept with the run (never only "failed")
            if lesson_revision:
                session.result = copy.deepcopy(result)
                entry.update(status='done', findingRevision=copy.deepcopy(result['findingRevision']), presentationPending=True)
            receipt_status, receipt_result = entry["status"], entry.get("result")
            if lesson_revision:
                receipt_result = copy.deepcopy(result)
            if attempt is not None:
                entry.update(attempt.facts(), cause=cause)
            session.history.append(entry)
            session.status, session.run, session.last = run.status_before, None, entry
            if run.kind == "answer" and result:
                self._ledger(session, {"kind": "answer", "question": entry["brief"], "title": result.get("title"),
                                       "text": result.get("summary"), "sources": result.get("sources") or []})
            elif run.kind == "revise" and entry.get("output"):
                new = session.output(entry["output"]) or {}
                self._ledger(session, {"kind": "revision", "output": new.get("id"), "replaces": new.get("replaces"),
                                       "version": new.get("version"), "change": entry["change"]})
        else:
            if result is None and session.finding_revisions and session.result:
                result = copy.deepcopy(session.result)  # completed science survives presentation failure
            found = session.findings[session._before.get("findings", 0):]
            ran = [r for r in session.ran if int(r.get("run") or 0) == session.run_number() and r.get("id")]
            if ((made or found or ran) and result is None and session.purpose == "research"
                    and status in ("timeout", "over_budget", "interrupted")):
                # Stopped (watchdog, worker gone) after finding, making or measuring something: keep the work.
                result, status = partial_result(session.brief, session.label, made, status, findings=found,
                                                operations=ran), "partial"
            session.status = status
            session.result = result
            session.package = ""  # given to that run only
            session.result_run = {**attempt.facts(), "cause": cause} if attempt is not None else None
            session.finished = time.time()
            if result and session.purpose == "research":
                self._ledger(session, {"kind": "result", "title": result.get("title"), "text": result.get("summary"),
                                       "sources": result.get("sources") or []})
            receipt_status, receipt_result = status, result
        claims = [c for c in (result or {}).get("claims") or [] if isinstance(c, dict)]
        if claims:
            self._log(session, {"kind": "claims-check", "claims": len(claims),
                                "found": sum(c.get("found") is True for c in claims),
                                "not_found": sum(c.get("found") is False for c in claims),
                                "unchecked": sum(c.get("found") is None for c in claims)})
        if attempt is not None:
            # Resolve the dispatched run before listeners can queue another one and mutate Session.
            # Its projected partial status and actual timeout cause are separate retained facts.
            attempt.record.update(status=receipt_status, cause=cause, hasResult=bool(receipt_result), completedAt=time.time())
            outcome = {"status": receipt_status, "cause": cause, "result": copy.deepcopy(receipt_result), "run": attempt.facts(),
                       "mode": receipt_mode,
                       "outputs": [{"id": o.get("id"), "version": o.get("version") or 1} for o in made],
                       "producedOutputs": copy.deepcopy(made)}
        try:
            self._save(session)
        except OSError:
            session.status = 'failed'
            session.step = 'Could not save the result. Keep this view open; storage must recover before saving.'
            if attempt is not None and not attempt.completion.done():
                attempt.completion.set_result({'status': 'failed', 'cause': 'persistence_failed', 'result': None,
                                              'outputs': [], 'producedOutputs': [], 'run': attempt.facts()})
            self._changed(session)
            raise
        if attempt is not None:
            if not attempt.completion.done():
                attempt.completion.set_result(outcome)
        self._changed(session)
        # Optional presentation work starts only after the scientific result and its
        # completion dependency are retained/released. Reopening never enters here.
        automatic_format = session.deliverable in ('report', 'lesson') or (session.deliverable == 'research'
                            and bool((session.result or {}).get('tables') or (session.result or {}).get('charts')))
        if ((receipt_mode is None or lesson_revision) and session.purpose == 'research' and automatic_format
                and not (attempt is not None and attempt.origin.get('study'))
                and status in ('done', 'partial') and ((session.result or {}).get('report') or (session.result or {}).get('lesson'))
                and getattr(self.hub.settings, 'presentation', 'off') != 'off'):
            basis = session.result.get('findingRevision')
            if basis:
                try:
                    kind = 'lesson' if session.deliverable == 'lesson' and session.result.get('lesson') else 'report'
                    current = self.presentations.current(session, kind)
                    self.presentations.begin(session.rid, f"findings-{basis['version']}-{basis['digest'][:20]}",
                        version=basis['version'], digest=basis['digest'], expected_base=(current or {}).get('id'), kind=kind, notify=True,
                        automatic=True)
                except (ResearchRefused, OSError):
                    log.exception('optional presentation admission failed; findings remain retained')
        completed = session.completed_view()
        for hook in self.on_done:
            try:
                hook(completed)
            except Exception:  # a broken listener never stops a researcher; it is logged
                log.exception('research listener failed')
        self._ask_pending(session)
        self._pump()

    def _ask_pending(self, session: Session) -> None:
        """A question kept while it worked is asked now that the run has ended (one at a time); stopped, or of a session
        no longer open (only this session's work is continued), it is said and dropped, never run."""
        if not session.pending or session.status in ACTIVE:
            return
        kept = session.pending.pop(0)
        if session.status == "cancelled" or session.sitting != self.session():
            why = ("it was stopped" if session.status == "cancelled"
                   else f"another session was open when it finished; ask it again in “{self.session_name(session.sitting)}”")
            session.step = f"Its kept question was not asked ({why}): {kept['question']}"
            session.pending.clear()
            self._save(session)
            self._changed(session)
            return

        async def ask() -> None:
            try:
                await self.followup(session.rid, kept["question"], origin=kept.get("origin"), mode="answer",
                                    words=kept.get("words") or "", asked_with=kept.get("asked_with") or "",
                                    delivery=getattr(session, "_pending_delivery", None), selection=kept.get("selection"))
            except ResearchRefused as exc:
                session.step = f"Its kept question could not be asked: {exc}"
                self._save(session)
                self._changed(session)

        asyncio.ensure_future(ask())

    async def _request(self, message: dict) -> dict:
        op = str(message.get("op") or "")
        # The builder's workbench (draft_write, draft_pin, draft_try: _draft_op) is a request too (acceptance 2026-10-14:
        # a merge left it out here, so every real builder run was told "unknown request" and drafted nothing).
        if op not in ("snapshot", "run", "save_findings") and not op.startswith("draft_"):
            return {"ok": False, "error": f"unknown request {message.get('op')}"}
        session = self.sessions.get(message.get("rid"))
        if session is None:
            return {"ok": False, "error": "no such researcher"}
        if message.get('op') == 'save_findings':
            attempt = session._attempt
            if (session.status not in ACTIVE or attempt is None or attempt.completion.done()
                    or message.get('runId') != attempt.id or not attempt.record.get('dispatchedAt')):
                return {'ok': False, 'error': 'the original research run is no longer active'}
            before_result, before_records = copy.deepcopy(session.result), copy.deepcopy(session.finding_revisions)
            try:
                self._event({**message, 'event': 'findings'})
            except (OSError, ValueError) as exc:
                session.result, session.finding_revisions = before_result, before_records
                session.step = 'Its findings were not saved yet; keep this view open while storage recovers'
                self.log({"kind": "research-findings-unsaved", "rid": session.rid, "error": f"{type(exc).__name__}: {exc}"[:300]})
                self._changed(session)
                return {'ok': False, 'error': session.step}
            return {'ok': True, 'findingRevision': copy.deepcopy(session.result['findingRevision'])}
        if str(message.get("op") or "").startswith("draft_"):  # the builder's workbench (drafts.py, C4)
            return await self._draft_op(session, message)
        if message.get("op") == "run":  # the app's own operations, for its reading (hands.py)
            if message.get('operation') in ('measure_saved_objects','workflow'):
                attempt = session._attempt
                if (attempt is None or message.get('runId') != attempt.id or attempt.completion.done()
                        or not attempt.record.get('dispatchedAt')):
                    return {'ok': False, 'error': 'The original research run is no longer active'}
            if session.status not in ("starting", "running"):
                return {"ok": False, "error": "it has finished"}
            if "run" not in EFFORTS[session.run_effort].tools or session.run is not None:
                # Its tool list is the worker's gate; this is the app's own (an answer or a revision runs none).
                return {"ok": False, "error": "this run of it does not run the app's operations"}
            # What it waits on, said truly: a segment waits for the live view's own GPU work; enhance is the app's
            # own CPU work, made at once (#186 review note 2).
            before, session.step = session.step, {
                "enhance": "making a clearer picture from real frames",
                "measure_saved_objects": "measuring the selected saved labels",
                "workflow": "using the selected saved workflow",
                "motion": "watching the live view to measure what moves"}.get(
                str(message.get("operation") or ""), "waiting for the live view: the app's own work goes first")
            self._changed(session)
            try:
                frame, seconds = message.get("frame"), message.get("seconds")
                reply = await self.hands.run(session, str(message.get("operation") or ""), str(message.get("why") or ""),
                                             wait=message.get("wait") if isinstance(message.get("wait"), (int, float)) else None,
                                             input=str(message["input"])[:80] if message.get("input") else None,
                                             frame=frame if isinstance(frame, int) and not isinstance(frame, bool) else None,
                                             how=str(message["how"])[:20] if message.get("how") else None,
                                             seconds=seconds if isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
                                             else None, fresh=message.get("fresh", False),
                                             **({"implementation": str(message["implementation"])[:40]}
                                                if message.get("implementation") else {}),  # segment's cell finder (Q24)
                                             **({"resource": message["resource"]} if "resource" in message else {}),
                                             **({"inputs": message["inputs"]} if "inputs" in message else {}),
                                             **({"selection": message["selection"]} if "selection" in message else {}),
                                             **({'run_id': message.get('runId')}
                                                if message.get('operation') in ('measure_saved_objects','workflow') else {}),
                                             **({'params':message.get('params')} if message.get('operation')=='workflow' else {}),
                                             # one the catalog added, with its parameters (the one door, kinds.py)
                                             **({"capability": str(message["capability"])[:120],
                                                 "params": message.get("params") if isinstance(message.get("params"), dict)
                                                 else None} if message.get("capability") else {}))
            finally:
                if session.step.startswith(("waiting for the live view", "making a clearer picture", "watching the live view",
                                            "measuring the selected saved labels", "using the selected saved workflow")):
                    session.step = before
                    self._changed(session)
            if reply.get("ok"):
                self._save(session)
                self._changed(session)
            return reply
        if time.monotonic() - session.last_snapshot < SNAPSHOT_EVERY_S:
            return {"ok": False, "error": "one snapshot per 10 s"}
        obj = self.hub.scene.objects.get(session.object_id) if session.object_id else None
        if session.object_id and (obj is None or obj.track != "tracking" or not self._in_view(obj)):
            return {"ok": False, "error": "the object is not in view right now"}
        if not self.hub.source.is_fresh():
            return {"ok": False, "error": "no fresh picture from the camera"}
        session.last_snapshot = time.monotonic()
        directory = self.root / session.day / session.rid
        meta = await self._take_snapshot(directory, obj)  # fresh acquisition captures its own current context
        return {"ok": True, "frame": meta.get("frame"), "crop": meta.get("crop")}

    async def _draft_op(self, session, message: dict) -> dict:
        """What a builder asks of the app (drafts.py): its draft written, a file pinned, the draft tried in the sandbox.
        Only a running builder (deliverable "adapter") with a draft of its own is answered."""
        from .. import drafts

        did = getattr(self, "drafts", {}).get(session.rid)
        if did is None or session.deliverable != "adapter":
            return {"ok": False, "error": "this researcher drafts no adapter"}
        if session.status not in ("starting", "running"):
            return {"ok": False, "error": "it has finished"}
        op = message.get("op")
        from .. import owned_process

        def stop() -> bool:  # the researcher's own Stop, its session closed or finished: its draft work ends with it
            return session.status not in ("starting", "running")

        try:
            if op == "draft_write":
                fields = message.get("fields") if isinstance(message.get("fields"), dict) else {}
                return await asyncio.to_thread(drafts.write, did, str(message.get("adapter") or ""),
                                               str(message.get("requirements") or ""), fields)
            if op == "draft_pin":  # owned: Stop and the builder's budget end the download (drafts.py)
                return {"ok": True, **await owned_process.off_loop(drafts.pin, did, str(message.get("url") or ""),
                                                                   stop=stop)}
            if op == "draft_try":
                session.step = "trying its draft on the retained input, inside the sandbox"
                self._changed(session)
                from ..capture import captures
                return await owned_process.off_loop(drafts.attempt, did, stop=stop, memory=captures(self.hub).memory)
        except drafts.DraftError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": f"no such step {op!r}"}

    def _in_view(self, obj) -> bool:
        info = self.hub.latest_info
        if info is None or info.fov is None:
            return info is not None
        x0, y0, x1, y1 = object_box_in(obj, info)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        return float(np.hypot(cx - info.fov.cx, cy - info.fov.cy)) < info.fov.radius

    # --- records ----------------------------------------------------------------------
    def _save(self, session: Session) -> None:
        directory = self.root / session.day / session.rid
        directory.mkdir(parents=True, exist_ok=True)
        # `session`: the session it was started in (ADR 0017), read back as `sitting`.
        data = session.wire() | {"origin": session.origin, "responseId": session.response_id, "session": session.sitting}
        if session.admission is not None:
            data["admission"] = copy.deepcopy(session.admission)
        if session.asking:
            data["asking"] = session.asking
        if session.pending:
            data["pending"] = [{k: v for k, v in q.items() if k in ("question", "at")} for q in session.pending]
        data["findings"] = list(session.findings)  # all of them (the wire sends the last 20), so a restart keeps them
        data['findingRecords'] = copy.deepcopy(session.finding_revisions)
        data['usageRuns'] = copy.deepcopy(session.usage_runs)
        data['evidenceReads'] = copy.deepcopy(session.evidence_reads)
        data['evidenceReadsMissing'] = session.evidence_reads_missing
        if session.run is not None:  # answering or revising: on disk it is still the work it was
            data["status"] = session.run.status_before
            data["canFollowUp"] = session.purpose == "research"
        # Atomic: written beside it, then moved over it, so a stop mid-write never leaves a torn file (a mission's
        # ledger is saved row by row).
        tmp = directory / "session.json.tmp"
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, directory / "session.json")

    def _log(self, session: Session, record: dict) -> None:
        try:
            self.log({"rid": session.rid, "researcher": session.name, "effort": session.run_effort, **record})
        except Exception:  # logging never stops a researcher
            log.exception("research log failed")

    def _ledger(self, session: Session, entry: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        record = {"at": round(time.time(), 2), "rid": session.rid, "researcher": session.name, "label": session.label,
                  "objectId": session.object_id, "specimen": self.hub.scene.context.get("specimen"), **entry}
        with (self.root / "findings.jsonl").open("a") as f:
            f.write(json.dumps(record) + "\n")

    def _changed(self, session: Session) -> None:
        for hook in self.on_change:
            try:
                hook(session)
            except Exception:  # a broken listener never stops a researcher; it is logged
                log.exception("research listener failed")

    def stop(self) -> None:
        self.presentations.stop()
        if self._worker is not None:
            self._worker.stop()


def _door() -> dict:
    """What a researcher can run beside the app's own operations, as it starts (the one door, kinds.py; ADR 0024): the
    capabilities on here that take the view, with their parameters, and every cell finder segment can name. The research
    process imports nothing of the app, so it is told."""
    out = {}
    try:
        from .. import kinds

        out["capabilities"] = [{"key": k["key"], "name": k["name"], "says": k["says"], "params": k["params"]}
                               for k in kinds.available() if k.get("runnable") and k["input"] == "image.2d.rgb8"]
    except Exception:  # noqa: BLE001 - a catalog that can't be read now offers none, never stops a researcher
        pass
    try:
        from ..operations import OPERATIONS

        out["cellFinders"] = {i.id: i.label for i in OPERATIONS["cells"].implementations}
    except Exception:  # noqa: BLE001
        pass
    return out
