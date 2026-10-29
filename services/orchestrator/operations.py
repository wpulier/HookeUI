"""Operations: the kinds of work the app does, the implementations that can do each, and the one
runner that chooses one, runs it, falls back only as declared and records what ran.

docs/design/2026-10-06-captures-operations-evidence.md, contracts 4, 6 and 7; ADR 0011.

- An `Operation` is a kind of work with a contract: what it returns (a schema with a version), the
  option group that picks its default (or none: fixed in the code), its implementations and its
  declared fallbacks. An operation can be a model, an algorithm or a composite.
- An `Impl` is one way to do it, with the prompts it accepts, the worker class it runs in
  (interactive, analysis, follow, molmo, cloud, cpu), what it needs (licence components, a key) and
  its version.
- A `Fallback` says after which implementations, when (`empty-box`, `unsure-spot`,
  `empty-refinement`, `unavailable`), and to what.
- Every run has an `origin` (who asked: click, brain-default, brain-chosen, user-chosen, arena,
  compare, background, replay: the replay bench running it again on a past session's view, or check: the capability
  check running each model once, model_checks.py) and a `policy`: `raw` runs exactly that implementation or fails; `route` adds
  its declared fallbacks. Defaults run `route`; an explicit choice runs `raw` (ADR 0011).
- A `Job` is one computation: capture, normalized prompt, implementation and version, parameters,
  preprocessing and policy. Requests that need the same job share it and keep their own tickets
  (contract 4); there is one producing `Execution` per job, and each request's use of it is recorded,
  reused or not, so a reuse is never presented as a fresh run.

The runner builds the Execution and the small "made by" the models see itself; the execution log
(execution.py) only observes, and the runner behaves the same with it on or off.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import execution, user_stop
from .brain.registry import ToolFailed

PROMPTS = ("point", "box", "field", "example", "text", "clip")
ORIGINS = ("click", "brain-default", "brain-chosen", "user-chosen", "arena", "compare", "background", "replay", "check",
           "headless")  # headless: a caller with no studio, camera or screen (capability_runs.run_on_resource; hosted AA-01)
POLICIES = ("raw", "route")
WORKERS = ("interactive", "analysis", "follow", "molmo", "cloud", "cpu")
# Contract 5's click bound rests on the short ops: the newest click waits at most for the one running
# when it came, then its own, so the click's 300 ms p95 holds while every short op's worker time stays
# under this at p99. bench/short_ops_bench.py measures each interactive implementation and the follow
# worker's tracking step against it (and fails when one passes it).
SHORT_OP_P99_LIMIT_MS = 150.0


# How long the user's Stop waits for a stopped job to end before it is answered (an adapter's process is reaped within
# owned_process's own bounds: two waits of REAP_S and a read of the process table). Past it the Stop is answered
# "stopping", never "stopped", and the job is followed until it ends (Runner._stopped).
STOP_WAIT_S = 5.0
# An attempt's error that is a raw exception ("RuntimeError: …", `_attempt`), not words for the user.
RAW_ERROR = re.compile(r"^[A-Z][A-Za-z]*(?:Error|Exception|Fault|Exit|Interrupt|Timeout)\b: ")


class InputUnread(ToolFailed):
    """An implementation's input could not be read (a view, masks, pictures or looks kept for it): it never ran. Said in
    plain words like any refusal; outcomes say it apart from a refusal (K3 re-check NOTE 1)."""


class Unavailable(ToolFailed):
    """The implementation cannot run here now; the message says why and what can."""

    def __init__(self, message: str, fallback: str | None = None, execution: "Execution | None" = None,
                 next: str | None = None) -> None:
        super().__init__(message, next=next)
        self.fallback = fallback  # a declared fallback the caller carries out (find-like: the vision model's boxes)
        self.execution = execution


class Unsupported(Unavailable):
    """It ran and refused this input, in words (a capability's {"unsupported": "why"}; catalog format 2's outcomes):
    said as "doesn't take this", counted as refused, never as the model's failure."""


@dataclass(frozen=True)
class Impl:
    id: str
    label: str
    kind: str  # "model" | "algorithm" | "composite"
    accepts: tuple[str, ...]
    worker: str
    option: str | None = None  # the option (in the operation's group) that selects it as the default
    uses: tuple[str, ...] = ()  # licences.COMPONENTS ids
    needs: str = ""  # an environment key it cannot run without
    version: str = ""
    steps: tuple[str, ...] = ()  # a composite's implementations, in order
    note: str = ""  # how it takes a prompt kind, when that is not obvious (stated, never hidden)
    installed: str = ""  # what must be installed here for it to run: a key of INSTALLED (its check says what is missing)
    weights: str = ""  # fine-tuned weights it cannot run without (finetuned.py): the newest whole version is used
    # It runs in a process of its own (a Fiji function: kinds.Backend.own_process), so no GPU worker is asked whether
    # it can run: its worker class only says how it is scheduled.
    own_process: bool = False


IMPL_MODEL: dict[str, str] = {}  # implementation id -> the catalog entry it runs (filled by _model)


def _model(impl_id: str, accepts: tuple[str, ...], worker: str, option: str | None = None, *, model: str = "",
           label: str = "", **kw) -> Impl:
    """An implementation that runs a catalog model (catalog/models/<id>; ADR 0018): its name, version, licence
    components, key and install come from the model's folder, so they are written once; `label` and `version` only where
    this implementation is a different use of it (EdgeTAM's automatic masks, SAM 3.1's every match)."""
    from gpu_worker.manifests import get

    entry = get(model or impl_id)
    if entry is None:
        raise ValueError(f"implementation {impl_id}: no catalog model {model or impl_id!r}")
    kw.setdefault("version", entry.version)
    kw.setdefault("needs", entry.needs)
    kw.setdefault("installed", entry.installed)
    kind = kw.pop("kind", "model")  # "algorithm" for a reviewed binding of a tool (Fiji's Analyze Particles)
    IMPL_MODEL[impl_id] = entry.key  # which catalog entry it runs (made_with.py: what a shared picture names)
    return Impl(impl_id, label or entry.name, kind, accepts, worker, option, entry.licence, **kw)


@dataclass(frozen=True)
class Fallback:
    after: tuple[str, ...]
    when: str  # "empty-box" | "unsure-spot" | "empty-refinement" | "unavailable"
    to: str
    needs: str = ""


@dataclass(frozen=True)
class Operation:
    id: str
    title: str
    returns: str  # schema/version
    selected_by: str | None
    implementations: tuple[Impl, ...]
    fallbacks: tuple[Fallback, ...] = ()
    empty_is_result: bool = False  # finding nothing is an answer (cells, matches), not a failure to outline
    # The keys of its output that hold what it made: a run whose output has none of them made nothing ("empty").
    # Enrichments (K1) make vectors, groups, numbers and rankings, not polygons (review B2).
    output: tuple[str, ...] = ("polygons", "matches")

    def impl(self, impl_id: str) -> Impl | None:
        return next((i for i in self.implementations if i.id == impl_id), None)

    def selectable(self) -> list[Impl]:
        return [i for i in self.implementations if i.option is not None]


_POINTS = ("point", "box")
OUTLINE = Operation(
    "outline", "Outline a thing from a click or a box", "instance/1", "outline",
    (
        _model("edgetam", _POINTS, "interactive", "edgetam"),
        _model("sam2.1-tiny", _POINTS, "interactive", "sam2.1-tiny"),
        _model("sam3.1-local", ("point", "box", "text"), "analysis", "sam3.1-local",
               note="a point is sent as a 32 px box around it; first use downloads ~3.5 GB"),
        _model("sam3.1-fal", ("point", "box", "text"), "cloud", "sam3.1-fal", model="sam3.1-cloud",
               note="a point is sent as a 32 px box around it"),
        _model("micro-sam-lm", _POINTS, "analysis", "micro-sam-lm"),
        Impl("edgetam>sam3.1-fal", "EdgeTAM then SAM 3.1 (cloud)", "composite", _POINTS, "cloud", "edgetam>sam3.1-fal",
             ("edgetam", "sam3.1", "fal-api"), needs="FAL_KEY", steps=("edgetam", "sam3.1-fal"),
             note="a box goes straight to SAM 3.1; a point is seeded by EdgeTAM's box"),
        Impl("edgetam>sam3.1-local", "EdgeTAM then SAM 3.1 (this Mac)", "composite", _POINTS, "analysis",
             "edgetam>sam3.1-local", ("edgetam", "sam3.1"), steps=("edgetam", "sam3.1-local"),
             note="a box goes straight to SAM 3.1; a point is seeded by EdgeTAM's box"),
        Impl("spot-ring", "A ring around the detected spot", "algorithm", ("point",), "cpu",
             note="only as a declared fallback: a clean ring around a small spot the outline model was unsure of"),
    ),
    (
        Fallback(("edgetam", "sam2.1-tiny"), "empty-box", "sam3.1-fal", "FAL_KEY"),
        Fallback(("edgetam", "sam2.1-tiny", "sam3.1-local", "sam3.1-fal", "micro-sam-lm", "edgetam>sam3.1-fal",
                  "edgetam>sam3.1-local"), "unsure-spot", "spot-ring"),
        Fallback(("edgetam>sam3.1-fal", "edgetam>sam3.1-local"), "empty-refinement", "edgetam"),
    ),
)
CELLS = Operation(
    "cells", "Segment every cell in the view", "polygons/1", "cells",
    (
        _model("cellpose-sam", ("field",), "analysis", "cellpose-sam"),
        _model("cellpose-sam-tuned", ("field",), "analysis", "cellpose-sam-tuned", weights="cellpose-sam-tuned",
               note="the newest version made by make finetune-cells from your corrected cells; its version is in what it made"),
        _model("micro-sam-lm", ("field",), "analysis", "micro-sam-lm"),
        # A reviewed binding of the managed Fiji (fiji_bindings.py): particles above ImageJ's threshold, not cells.
        _model("fiji-analyze-particles", ("field",), "analysis", "fiji-analyze-particles", kind="algorithm", own_process=True),
        _model("basic-bright-objects", ("field",), "cpu", "basic-bright-objects", kind="algorithm"),
        _model("edgetam-automask", ("field",), "analysis", "edgetam-automask", model="edgetam", label="EdgeTAM automatic masks",
               note="masks may overlap; a stated arbitration draws them"),
    ),
    empty_is_result=True,
    output=("polygons", "count"),
)
FIND_LIKE = Operation(
    "find-like", "Find every thing like an example", "matches/1", None,
    (
        _model("sam3.1-all", ("example", "text"), "cloud", model="sam3.1-cloud", label="SAM 3.1 every match (cloud)",
               version="fal-ai/sam-3-1/image-rle"),
        Impl("vision-boxes", "The vision model's boxes outlined", "composite", ("example", "text"), "cloud",
             note="the vision model boxes each thing and the outline model draws each box (the finder does it)"),
    ),
    (Fallback(("sam3.1-all",), "unavailable", "vision-boxes"),),
    empty_is_result=True,
)
TRACK = Operation(
    "track", "Follow outlined things from frame to frame", "tracks/1", None,
    (_model("edgetam-video", ("box",), "follow",
            note="a session, stepped ten times a second; only its starts, stops and resets are recorded"),),
)
# What moves, measured (M3.2): an algorithm on the frames of the last seconds, kept as a tracks item
# (motiontracks.py). Its version names the code and its parameters (motiontracks.MOTION_VERSION).
MOTION = Operation(
    "motion", "Measure what moves in the view", "motion-tracks/1", None,
    (Impl("spots", "Small spots followed frame to frame", "algorithm", ("clip",), "cpu", version="orchestrator.movement blobs/1",
          note="the slide's own motion taken out; speeds from paths smoothed over 5 frames"),),
    empty_is_result=True,  # nothing followed is a measurement too ("nothing could be tracked")
    output=("tracks",),
)
LINK = Operation(
    "link", "Link the cells of two views (Trackastra's guess of which is the same cell)", "links/1", None,
    (_model("trackastra", ("populations",), "cpu",
            note="two populations' label maps and views in, links between their members out, each with its score; an "
               "implementation for session knowledge's identity (K2), not yet judged on our specimens"),),
    empty_is_result=True,
    output=("links",),  # a run that linked anything made something (M3.5 review S6)
)
# Enrichments (session knowledge K1, orchestrator/enrichment.py): facts added to a population's members as
# column groups beside the measurements; members in, one column group out.
LOOKS = Operation(
    "looks", "Embed how each member looks", "vectors/1", "embed",
    (_model("dinov2-small", ("members",), "analysis", "dinov2-small"),
     _model("bioclip-2", ("members",), "analysis", "bioclip-2"),
     # Gated weights (Meta's licence): HF_TOKEN gets them (a need to get, its manifest); a downloaded copy runs without
     # it, and until it is downloaded `use` refuses it with the Download step, as the Models page says (readiness).
     _model("dinov3-small", ("members",), "analysis", "dinov3-small")),
    output=("vectors",),
)
GROUPS = Operation(
    "groups", "Group members by how they look", "groups/1", None,
    (Impl("kmeans-silhouette", "k-means (groups by silhouette)", "algorithm", ("members",), "cpu", version="1",
          note="over one model's looks only; 2-8 groups, the silhouette score reported; a group is never a type"),),
    output=("groups",),
)
LIGHT = Operation(
    "light", "Measure the colour inside each member", "numbers/1", None,
    (Impl("channel-stats", "Mean and spread of each colour channel", "algorithm", ("members",), "cpu", version="1"),),
    output=("values",),
)
NAMES = Operation(
    "names", "Rank the subject's candidate names for each group", "rankings/1", None,
    (_model("medsiglip-448", ("members",), "analysis", model="medsiglip", note="names as data with their calibration; blood and Pap smears only"),),
    output=("byGroup",),
)
YOUR_LABEL = Operation(
    "your-label", "Find each member's nearest group the user named", "labels/1", None,
    (Impl("nearest-named", "The nearest named group by look", "algorithm", ("members",), "cpu", version="1",
          note="within one model's looks; a label only from that model's measured floor, none where it has none"),),
    output=("values",),
)
# The operations and their adapters change while the app runs (entries proven or withdrawn: kinds.register, on the loop
# and in capability_runs' thread; your models turned on or off: mine.register, in App.sync_mine's thread) while readers
# anywhere iterate them (catalogue through option_not_installed, kinds.register itself, label_of, names). So neither is
# ever changed in place: each write builds a new dict and swaps it in under one writers' lock (Registry), and a reader
# iterating one keeps a whole dict that never changes. The Registry object itself is never replaced, so a name imported
# at load (`from .operations import OPERATIONS`) stays current; an Operation constant (OUTLINE, CELLS...) is only the
# operation as the code declares it, so read one as it is now through OPERATIONS[id]. A change built on what an
# operation was (its implementations) goes through change_operation, so two writers never lose each other's.
# Review of #413, 2026-10-21: "dictionary changed size during iteration".
_WRITE = threading.RLock()  # writers only, held for a dict copy (readers never wait); reentrant: change_operation


class Registry(MutableMapping):
    """A dict that is copied on every write and swapped in whole: iterating it (or a view of it) never sees a change."""

    __slots__ = ("_now",)

    def __init__(self, items: Mapping | Iterable = ()):
        self._now = dict(items)

    def change(self, put: Mapping | None = None, drop: Iterable = ()) -> bool:
        """Add or replace (`put`) and take away (`drop`) entries in one swap. Returns whether anything changed."""
        def edit(d: dict) -> bool:
            before = dict(d)
            for key in drop:
                d.pop(key, None)
            d.update(put or {})
            return d != before
        return self._write(edit)

    def _write(self, edit: Callable[[dict], Any]) -> Any:
        """The one writer: `edit` changes a copy, which is swapped in whole (an edit that raises changes nothing)."""
        with _WRITE:
            new = dict(self._now)
            out = edit(new)
            self._now = new
            return out

    # Reads: the dict as it is now; an iterator or a view taken from it keeps that dict.
    def __getitem__(self, key):
        return self._now[key]

    def __iter__(self):
        return iter(self._now)

    def __len__(self) -> int:
        return len(self._now)

    def __contains__(self, key) -> bool:
        return key in self._now

    def get(self, key, default=None):
        return self._now.get(key, default)

    def keys(self):
        return self._now.keys()

    def values(self):
        return self._now.values()

    def items(self):
        return self._now.items()

    def copy(self) -> dict:
        return dict(self._now)

    def __repr__(self) -> str:
        return f"Registry({self._now!r})"

    # Writes: each one a new dict, swapped in.
    def __setitem__(self, key, value) -> None:
        self._write(lambda d: d.__setitem__(key, value))

    def __delitem__(self, key) -> None:
        self._write(lambda d: d.__delitem__(key))

    _MISSING = object()

    def pop(self, key, default=_MISSING):
        return self._write(lambda d: d.pop(key) if default is Registry._MISSING else d.pop(key, default))

    def setdefault(self, key, default=None):
        return self._write(lambda d: d.setdefault(key, default))

    def update(self, other=(), /, **more) -> None:
        self._write(lambda d: d.update(other, **more))

    def clear(self) -> None:
        self._write(lambda d: d.clear())


OPERATIONS: Registry = Registry((op.id, op) for op in (OUTLINE, CELLS, FIND_LIKE, TRACK, MOTION, LINK, LOOKS, GROUPS, LIGHT,
                                                        NAMES, YOUR_LABEL))


@dataclass
class Prompt:
    kind: str
    x: float | None = None
    y: float | None = None
    box: tuple[float, float, float, float] | None = None
    text: str = ""
    prefer_small: bool = False
    spot: Any = None  # the detected spot a click snapped to (its centre and radius), for the spot ring
    # A "clip" prompt's frames, oldest first: ((frame, facts), ...), each frame's bytes read with its facts
    # when the clip was gathered (invariant 1); the job is the same only for the very same frames.
    clip: Any = None

    def key(self) -> tuple:
        r = lambda v: None if v is None else round(float(v), 1)  # noqa: E731
        spot = (r(getattr(self.spot, "x", None)), r(getattr(self.spot, "y", None)), r(getattr(self.spot, "radius", None))) \
            if self.spot is not None else None  # the spot changes the result (the ring's size)
        clip = tuple(int(getattr(info, "id", -1)) for _frame, info in self.clip) if self.clip else None
        return (self.kind, r(self.x), r(self.y), tuple(r(v) for v in self.box) if self.box else None, self.text,
                self.prefer_small, spot) + ((clip,) if clip is not None else ())


@dataclass
class Attempt:
    impl: str
    ok: bool
    empty: bool = False
    ms: float = 0.0
    error: str = ""
    why: str = ""  # "chosen", or the fallback that ran it
    # The next step an adapter's own refusal gave (a ToolFailed's `next`), said with the failure: "ask again"
    # only when it gave none (review note: "too few to group" is not helped by asking again). Not on the wire.
    next: Any = ...
    # Why the implementation never ran, when it did not (NOT_RUN): cancelled, superseded, busy, expired, unavailable or
    # refused. Outcomes (outcomes.py) count only attempts that ran; a failure is the model's own error (K3 review B2).
    not_run: str = ""

    def wire(self) -> dict:
        out = {"impl": self.impl, "ok": self.ok, "empty": self.empty, "ms": round(self.ms, 1), "why": self.why}
        if self.error:
            out["error"] = self.error[:200]
        if self.not_run:
            out["notRun"] = self.not_run
        return out


NOT_RUN = ("cancelled", "superseded", "busy", "expired", "hung", "unavailable", "unread", "refused")


def not_run_of(exc: BaseException) -> str:
    """Why an attempt that raised never ran its model ("" when the model ran and failed): nobody needed it any more,
    a newer one replaced it in the queue, the queue was full, it expired there, its worker was not answering (the call
    was never sent), it can't run here, its input could not be read, or the implementation refused its input in words
    (a ToolFailed) before running."""
    from .gpu_client import Busy, ExpiredError, Superseded, WorkerHungError

    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, Unsupported):
        return "refused"
    if isinstance(exc, Unavailable):
        return "unavailable"
    if isinstance(exc, Superseded):
        return "superseded"
    if isinstance(exc, Busy):
        return "busy"
    if isinstance(exc, ExpiredError):
        return "expired"
    if isinstance(exc, WorkerHungError):
        return "hung"
    if isinstance(exc, InputUnread):
        return "unread"
    if isinstance(exc, ToolFailed):
        return "refused"
    return ""


_boot = uuid.uuid4().hex[:6]
_counter = itertools.count(1)


@dataclass
class Execution:
    """What one job did: what was asked for, every attempt, and the implementation whose output was used."""
    operation: str
    requested: str
    policy: str
    origin: str
    capture: int | None
    selection: str  # "settings" | "explicit" | "fixed" | "request" (a one-request choice)
    id: str = field(default_factory=lambda: f"x{_boot}-{next(_counter)}")
    attempts: list[Attempt] = field(default_factory=list)
    executed: str | None = None
    outputs: list[str] = field(default_factory=list)
    at: float = field(default_factory=time.time)
    ms: float = 0.0
    subject: str | None = None
    trial: str | None = None  # an arena trial: identities stay off every surface until its vote
    revealed: bool = False  # its trial was voted on (Runner.reveal): Activity may name what ran (activity.blind)
    made_for: str | None = None  # who the run was for when not the user's request ("Researcher 2"; #173's note 9)
    version: str | None = None  # fine-tuned weights: the version that ran ("v2"), so outcomes tell versions apart (K4)
    producer: dict | None = None  # hosted method's retained input/settings/runtime/comparison receipt
    # Who started it (ticket 66, S2): "you" or "assistant", from the tool run or request that began it (_actor_now);
    # None when that cannot be told (a researcher's hands say `made_for` instead; background work says nobody).
    actor: str | None = None

    def made_by(self) -> dict:
        """The small fact the models see about where a result came from (never from the log)."""
        out = {"operation": self.operation, "implementation": self.executed, "policy": self.policy, "origin": self.origin,
               "execution": self.id}
        if self.actor:
            out["actor"] = self.actor
        if self.version:
            out["version"] = self.version
        if self.producer:
            out["producer"] = {k: self.producer[k] for k in ("catalog", "version", "generation") if k in self.producer}
        first = self.attempts[0].impl if self.attempts else None
        if self.executed is not None and first is not None and first != self.executed and \
                not (self.attempts[0].why == "chosen" and self.executed == self.requested):
            out["fallbackFrom"] = first
        return out

    def wire(self) -> dict:
        return {"id": self.id, "operation": self.operation, "requested": self.requested, "policy": self.policy,
                "origin": self.origin, "selection": self.selection, "capture": self.capture,
                "attempts": [a.wire() for a in self.attempts], "executed": self.executed, "outputs": list(self.outputs),
                "startedAt": round(self.at, 3), "ms": round(self.ms, 1), "subject": self.subject,
                **({"madeFor": self.made_for} if self.made_for else {}), **({"version": self.version} if self.version else {}),
                **({"producer": self.producer} if self.producer else {})}


def _actor_now(ticket) -> str | None:
    """Who starts a job now (ticket 66, S2), where the run begins: the tool run it is part of (brain/registry.py: the
    studio's Run is "you", the brain's chat and voice "assistant"), else the request's own channel (a click or the
    studio's Outline cells: "you"; the brain's fast look: "assistant"). None when neither says (background work)."""
    from .brain.registry import BRAIN_CHANNELS, run_actor

    actor = run_actor()
    if actor:
        return actor
    from .door import actor as door_actor
    if door_actor.current():  # work an outside agent started through the door (ADR 0026)
        return door_actor.current()
    channel = getattr(ticket, "channel", None)
    return "you" if channel == "studio" else "assistant" if channel in BRAIN_CHANNELS else None


@dataclass
class Use:
    """One request's use of a job's result."""
    execution: Execution
    reused: bool
    ticket: str | None


@dataclass
class Result:
    output: dict
    execution: Execution
    use: Use

    def made_by(self) -> dict:
        """The execution's made-by, saying when this request reused a result made for another (so
        nothing presents a reuse as a fresh run; invariant 5)."""
        return {**self.execution.made_by(), "reused": self.use.reused}


class Job:
    """One computation, shared by the requests that need it."""

    def __init__(self, key: tuple, task: asyncio.Task, execution: Execution, ticket=None) -> None:
        self.key, self.task, self.execution = key, task, execution
        self.ticket = ticket  # the request that started it (its execution line names it)
        self.subscribers: set[object] = set()  # one token per waiting request (two without a ticket stay two)
        self.started = False  # set when its work began (a GPU worker took it from its queue; any other at once)
        self.stopped = False  # the user's Stop ended it (user_stop.py), its begun work too: said "stopped", never failed
        self.cancelled = False  # the runner cancelled its task (nobody needs it, or the user's Stop): it is ending

    def ending(self) -> bool:
        """Being stopped or ending by cancellation: never joined, never kept for reuse (Runner.run, _finished)."""
        task = self.task
        return self.cancelled or self.stopped or (task is not None and (task.cancelled() or task.cancelling() > 0))


Adapter = Callable[["Runner", Any, Prompt, Impl, dict], Awaitable[dict]]
ADAPTERS: Registry = Registry()  # (operation, implementation) -> its adapter; copy-on-write as OPERATIONS


def change_operation(op_id: str, change: Callable[[Operation | None], Operation | None], *,
                     put: Mapping[str, Adapter] | None = None, drop: Iterable[str] = ()) -> bool:
    """The one way to change an operation from what it is now: `change` is given it (None: there is none) and returns it
    as it should be (None: taken away), under the writers' lock, so two writers (kinds.register, mine.register) never
    both build on the old one and lose the other's change. `put` and `drop`: its implementations' adapters, by
    implementation id, put before it changes and dropped after, so a reader never finds an implementation with no
    adapter. `change` only builds the operation (nothing slow: the lock is held). Returns whether it changed."""
    with _WRITE:
        if put:
            ADAPTERS.change(put={(op_id, impl): fn for impl, fn in put.items()})
        before = OPERATIONS.get(op_id)
        after = change(before)
        changed = after != before
        if changed:
            if after is None:
                OPERATIONS.pop(op_id, None)
            else:
                OPERATIONS[op_id] = after
        ADAPTERS.change(drop=[(op_id, impl) for impl in drop])
        return changed


def adapter(operation: str, *impls: str):
    def register(fn: Adapter) -> Adapter:
        for impl in impls:
            ADAPTERS[(operation, impl)] = fn
        return fn

    return register


_pth_seen: set[str] | None = None
_site_seen: tuple = ()


def _fresh_imports() -> None:
    """What was installed since the app started is importable: the import system's caches dropped, and any new `.pth`
    file processed as Python does at start (an editable install, `make sam3-setup`, works through one). Each `.pth`
    once, so an editable finder is never installed twice (the catalog audit's G5: an install made while the app ran
    stayed "not installed" until a restart). Only when a site-packages folder changed since the last look (an install
    adds entries to it, so its time changes): dropping the caches on every check made each later import list the import
    path again, which slowed the voice's requests (the acting bench's voice cases)."""
    import glob
    import importlib
    import site
    import sys

    global _pth_seen, _site_seen
    dirs = sorted(d for d in {*site.getsitepackages(), site.getusersitepackages()} if os.path.isdir(d))
    seen = tuple((d, os.stat(d).st_mtime_ns) for d in dirs)
    if seen == _site_seen:
        return
    _site_seen = seen
    importlib.invalidate_caches()
    found = {p for d in dirs for p in glob.glob(os.path.join(d, "*.pth"))}
    if _pth_seen is None:
        _pth_seen = found  # processed when this interpreter started
        return
    for path in sorted(found - _pth_seen):
        site.addpackage(os.path.dirname(path), os.path.basename(path), set(sys.path))
    _pth_seen |= found


def _package(name: str, how: str) -> str:
    import importlib.util

    _fresh_imports()
    return "" if importlib.util.find_spec(name) is not None else f"not installed here ({name}: {how})"


def _own_python(model: str) -> str:
    """"" when the Python the app made for `model` is ready (envs.py), else why not (then the app's own is looked in)."""
    from gpu_worker import manifests as catalog

    from . import envs

    m = catalog.get(model)
    return "" if m is not None and envs.python(m) is not None else "no environment of its own yet"


def _fiji_set_up() -> bool:
    from . import fiji_display as fiji_generations

    return fiji_generations.current() is not None


def _fiji_proven(binding: str) -> str:
    """A Fiji binding runs only once the generation in use recorded its proof (fiji_generations.proven)."""
    from . import fiji_display as fiji_generations

    return fiji_generations.proven(binding)


def _microsam_python() -> str:
    """micro-sam runs in its own Python: the one the app makes from its catalog lockfile (envs.py, its Install button),
    else one made by hand before (MICROMIND_MICROSAM_PYTHON), reached only through licences.py."""
    from . import licences

    try:
        found = licences.adapter("micro-sam-lm").python()
    except licences.NotInEdition as exc:
        return str(exc)
    return "" if found.exists() else "not installed here (micro-sam's own Python: its Install button on the Models page)"


# What must be installed here for an implementation to run (Impl.installed), each check saying what is missing and
# how to install it, "" when it is there. Live 2026-10-11: micro-sam and SAM 3.1 on this Mac were "available" whether
# or not their code was here. Weights are never fetched on first use: they are the catalog's download step.
INSTALLED: dict[str, Callable[[], str]] = {
    "sam3": lambda: _package("sam3", "`make sam3-setup`"),
    "trackastra": lambda: _own_python("trackastra") and _package("trackastra", "its Install button on the Models page"),
    "cellpose": lambda: _package("cellpose", "`make setup`"),
    "open_clip": lambda: _package("open_clip", "`make setup`"),
    "micro-sam": _microsam_python,
    "fiji": lambda: "" if _fiji_set_up() else "Fiji isn't set up here (its card on the Models page sets it up)",
    "fiji-analyze-particles": lambda: _fiji_proven("fiji-analyze-particles"),
}


def not_installed(impl: Impl) -> str:
    """What is missing for `impl` to run here, with how to install it ("" when nothing is)."""
    return INSTALLED[impl.installed]() if impl.installed else ""


def option_not_installed(group: str, option_id: str) -> str:
    """The same for an option that selects an implementation (OUTLINE, CELLS, EMBED), "" for any other option."""
    for op in OPERATIONS.values():
        if op.selected_by == group:
            impl = next((i for i in op.implementations if i.option == option_id), None)
            if impl is not None:
                return not_installed(impl) or next((not_installed(part) for step in impl.steps
                                                    if (part := op.impl(step)) is not None and not_installed(part)), "")
    return ""


def availability(hub, impl: Impl) -> str:
    """Why `impl` cannot run here now ("" when it can): this edition, a missing key, its code not installed here,
    its worker."""
    from . import readiness

    if impl.worker == "cloud":
        from research_worker import api_usage
        if getattr(hub, "local_only", lambda: False)():
            return api_usage.LOCAL_ONLY
        try:
            api_usage.require_provider()
        except api_usage.ProviderRefused as exc:
            return str(exc)
    why = readiness.implementation(impl)  # what can be said without running it (ADR 0019), then what only the run knows
    if why:
        return why
    for step in impl.steps:  # a composite runs only when every step can (its EdgeTAM step on the click worker too)
        part = next((i for op in OPERATIONS.values() for i in op.implementations if i.id == step), None)
        why = availability(hub, part) if part is not None else f"no {step}"
        if why:
            return why
    if impl.own_process:
        return ""
    client = worker_client(hub, impl.worker, start=False, model=LOAD_KEYS.get(impl.id, impl.id))
    if impl.worker in ("interactive", "analysis", "follow", "molmo"):
        if client is None:
            return "no GPU worker here"
        problem = getattr(client, "problem", lambda: "")()
        if problem:
            return problem
        if impl.worker == "interactive" and not getattr(client, "ready", False):
            return "the outline model is still loading"
    return ""


GPU_WORKERS = ("interactive", "analysis", "follow", "molmo")


def _work_words(op: "Operation", impl: "Impl") -> str:
    """One piece of work as a Stop names it: "Cells with Cellpose-SAM (on this Mac)"."""
    return f"{getattr(op, 'title', '') or op.id} with {impl.label}"


# An implementation's model as its worker loads it (gpu_worker/loading.py), where the two names differ.
LOAD_KEYS = {"edgetam-automask": "edgetam", "medsiglip-448": "medsiglip"}


def worker_client(hub, worker: str, start: bool = True, model: str | None = None):
    """The GPU worker process for a worker class (a follow worker falls back to the analysis one); for an analysis
    `model` whose manifest gives it a worker of its own, that one (hub.host_for)."""
    if worker == "interactive":
        return getattr(hub, "gpu", None)
    if worker == "analysis":
        host_for = getattr(hub, "host_for", None)
        if model is not None and callable(host_for):
            return host_for(model)
        return getattr(hub, "gpu_heavy", None) or getattr(hub, "gpu", None)
    if worker == "follow":
        return getattr(hub, "gpu_follow", None) or getattr(hub, "gpu_heavy", None)
    if worker == "molmo":
        return getattr(hub, "gpu_molmo", None)
    return None


async def started(client):
    """A worker client, started on first use where it starts lazily."""
    ensure = getattr(client, "ensure_started", None)
    return await ensure() if ensure is not None else client


class Runner:
    """The one place an operation is chosen, run, fallen back from (only as declared) and recorded."""

    REUSE_KEPT = 32

    def __init__(self, hub, log: Callable[[dict], None] | None = None) -> None:
        self.hub = hub
        self.log = log or (lambda record: None)
        self.jobs: dict[tuple, Job] = {}
        self.finished: OrderedDict[tuple, tuple[dict, Execution]] = OrderedDict()
        self.executions: OrderedDict[str, Execution] = OrderedDict()
        # Jobs the user stopped that had not ended when their Stop was answered (`_stopped`): kept until they end.
        self.stopping: set[asyncio.Task] = set()

    # --- choosing ---------------------------------------------------------------------------
    def choose(self, operation: str, prompt: Prompt, choice: str | None = None, policy: str | None = None,
               origin: str = "brain-default", ticket=None) -> tuple[Operation, Impl, str, str]:
        """(operation, implementation, policy, selection) for a run, or Unavailable with the reason
        and the compatible alternatives. Defaults are read through the settings (so a tool run
        records the read); an explicit choice is checked before any call."""
        op = OPERATIONS[operation]
        selection = "explicit" if choice else "settings"
        request_choice = _request_choice(ticket, operation, from_context=False)
        if choice is None and request_choice and request_choice.get("refused"):
            # The request asked for a model that was refused: nothing else does its work in its place.
            raise Unavailable(f"{request_choice.get('label')} was asked for and refused ({request_choice['refused']}), "
                              "so nothing else was used in its place", next="name another one, or ask without naming one")
        if choice is None and request_choice:
            choice, selection = request_choice.get("implementation"), "request"
            policy = policy or request_choice.get("policy")
        if choice is None:
            if op.selected_by is None:
                impl = op.implementations[0]
                selection = "fixed"
            else:
                value = getattr(self.hub.settings, op.selected_by)
                impl = next((i for i in op.implementations if i.option == value), None)
                if impl is None:
                    raise Unavailable(f"{op.title.lower()} is switched off in the settings", next="switch it on in the Behaviour menu, or ask for something else")
            policy = policy or "route"
        else:
            impl = op.impl(choice)
            if impl is None:
                raise Unavailable(f"there is no {choice} for {op.title.lower()}; it can use "
                                  + ", ".join(i.label for i in op.selectable()), next=None)  # no next: the message says what to do
            policy = policy or "raw"
        if policy not in POLICIES:
            raise ValueError(f"policy {policy!r}")
        if prompt.kind not in impl.accepts:
            fits = [i.label for i in op.selectable() if prompt.kind in i.accepts]
            raise Unavailable(f"{impl.label} can't take a {prompt.kind}; " + ("these can: " + ", ".join(fits) if fits else "nothing here can"), next=None)  # no next: the message says what to do
        why = availability(self.hub, impl)
        if why:
            fallback = next((f.to for f in op.fallbacks if f.when == "unavailable" and impl.id in f.after), None)
            alternatives = [i.label for i in op.selectable() if i is not impl and prompt.kind in i.accepts
                            and not availability(self.hub, i)]
            raise Unavailable(f"{impl.label} can't run here ({why})"
                              + (f"; available instead: {', '.join(alternatives)}" if alternatives and policy == "raw" else ""),
                              fallback=fallback if policy == "route" else None, next=None)  # no next: the message says what to do
        return op, impl, policy, selection

    # --- running ----------------------------------------------------------------------------
    async def run(self, operation: str, capture, prompt: Prompt, *, choice: str | None = None, policy: str | None = None,
                  origin: str = "brain-default", ticket=..., params: dict | None = None, trial: str | None = None,
                  made_for: str | None = None) -> Result:
        """Run `operation` on `capture` for `prompt` (contract 6): the default by route, or an explicit
        choice raw; a job that is already running or done with the same inputs is shared, not run
        again (contract 4). Raises Unavailable (with the reason) or ToolFailed (it ran and failed)."""
        from . import ticket as tickets

        from_context = ticket is ...
        ticket = tickets.current() if from_context else ticket
        workspace_origin = getattr(ticket, 'workspace_origin', None)
        if workspace_origin:
            expected = workspace_origin['input']
            source = getattr(capture, 'source_image', None) or {}
            actual = ({'resource': source['resource']} if source.get('resource') is not None
                      else {'source': source.get('asset') or 'live'})
            if expected != actual:
                raise Unavailable('The input changed before dispatch; the admitted work was not retargeted.', next='review the input and run again')
        if origin not in ORIGINS:
            raise ValueError(f"origin {origin!r}")
        chosen = _request_choice(ticket, operation, from_context) if choice is None else None
        if chosen and trial is None:
            # The request's own choice (`use`, contract 7): who chose it is the run's origin, and it is
            # marked used (a choice nothing used is said when the request ends).
            origin = chosen.get("origin") or origin
            chosen["used"] = True
        try:
            op, impl, policy, selection = self.choose(operation, prompt, choice, policy, origin, ticket if chosen else None)
        except Unavailable as exc:
            exc.execution = self._record(Execution(operation, choice or "default", policy or ("raw" if choice else "route"),
                                                   origin, getattr(capture, "id", None), "explicit" if choice else "settings",
                                                   attempts=[Attempt(choice or "default", False, error=str(exc), why="unavailable",
                                                                     not_run="unavailable")],
                                                   trial=trial, subject=self._subject()), ticket, reused=False)
            raise
        params = dict(params or {})
        if impl.weights:
            # Fine-tuned weights: the version this job runs is fixed when it is keyed, so a version written meanwhile
            # is another job, never shared with this one (the K4 review's O4).
            from . import finetuned

            found = finetuned.current(impl.weights)
            params["weightsVersion"] = found["version"] if found else None
        # The job is the computation: when it is scheduled (its priority) is not part of it, so a click
        # and a highlight of the same thing on the same view share one job (contract 4). A researcher's
        # own work ("research") is the exception: the user's requests never wait on a job queued below them.
        computed = {k: v for k, v in params.items() if k != "priority" or v == "research"}
        key = (getattr(capture, "id", None), prompt.key(), impl.id, impl.version, tuple(sorted(computed.items())), policy)
        ticket_id = getattr(ticket, "id", None)
        if trial is None and key in self.finished:
            output, done = self.finished[key]
            self.finished.move_to_end(key)
            if workspace_origin:
                from .workspace_views import link_execution
                link_execution(ticket.workspace_store(), workspace_origin, done.id)
            self._use(done, ticket, reused=True)
            return Result(output, done, Use(done, True, ticket_id))
        job = self.jobs.get(key) if trial is None else None
        if job is not None and job.ending():
            # A job being stopped (the user's Stop, or nobody needing it) is never joined: its end is a cancellation, and
            # a new request would inherit it (Codex's review of 8bd09b6a: a rerun right after "stopping"). This request
            # starts a fresh job in its place; the dying one keeps only its own done callback (_finished checks it).
            job = None
        reused = job is not None
        if job is None:
            record = Execution(op.id, impl.id, policy, origin, getattr(capture, "id", None), selection, trial=trial,
                               subject=self._subject(), made_for=made_for, actor=None if made_for else _actor_now(ticket))
            if workspace_origin:
                from .workspace_views import link_execution
                link_execution(ticket.workspace_store(), workspace_origin, record.id)
            job = Job(key, None, record, ticket)
            job.task = asyncio.ensure_future(self._execute(op, impl, policy, capture, prompt, params, record, job))
            if trial is None:
                self.jobs[key] = job
            job.task.add_done_callback(lambda t, k=key, j=job: self._finished(k, j, t))
        if reused and workspace_origin:
            from .workspace_views import link_execution
            link_execution(ticket.workspace_store(), workspace_origin, job.execution.id)
        token = object()
        job.subscribers.add(token)
        try:
            output = await asyncio.shield(job.task)
        except asyncio.CancelledError:
            # This request no longer wants it: its subscription goes; the job stops only if its
            # work has not begun and nobody else needs it (contract 4). A queued GPU call is then
            # dropped from the worker's queue; begun work completes.
            job.subscribers.discard(token)
            stopping = user_stop.requested()
            holder = user_stop.current() if stopping else None
            if holder is not None and job.subscribers:  # another request still needs it: never said as stopped
                holder.shared(f"{_work_words(op, impl)} (another request is waiting for the same result)")
            if not job.subscribers and (not job.started or stopping):
                # The user's own Stop (never a waiter that went away) of the job's only request stops begun work too:
                # an adapter's own process ends with its process group (owned_process), a GPU worker's call is
                # abandoned by its client (the worker, shared with clicks, finishes it and its reply is dropped).
                # Another request still waiting keeps the job: the Stop then only drops this one's subscription.
                job.stopped = job.stopped or (stopping and job.started)
                job.cancelled = True
                job.task.cancel()
                if holder is not None and job.started and impl.worker in GPU_WORKERS:
                    # A GPU worker's begun call is abandoned by its client, not ended: the worker (shared with clicks)
                    # finishes that step and its result is dropped. Said as discarded, never as work that stopped there.
                    holder.discarded(f"{_work_words(op, impl)}: its worker finishes the step it had begun, and that "
                                     "result is dropped")
                if stopping:
                    await self._stopped(job, user_stop.STOP.get(), _work_words(op, impl))
            raise
        finally:
            job.subscribers.discard(token)  # a request that has its result (or its error) no longer shares the job
        self._use(job.execution, ticket, reused=reused)
        return Result(output, job.execution, Use(job.execution, reused, ticket_id))

    async def _stopped(self, job: Job, stop, what: str = "") -> None:
        """The user's Stop cancelled `job`: wait up to STOP_WAIT_S for it to end (its process reaped), so the Stop is
        answered as done only when it is. A job still ending past that is never said to be stopped: it stays
        supervised (`stopping`, `stop.late`) until it ends, and how it ended is then logged and told to the Stop."""
        try:
            await asyncio.wait({job.task}, timeout=STOP_WAIT_S)
        except asyncio.CancelledError:
            pass  # cancelled again while it waited (the app stopping): the job is supervised below all the same
        if stop is None:
            return  # how it ended was read by its own done callback (_finished): nothing is left unretrieved
        if job.task.done():
            # It ended within the wait: how it ended is the Stop's answer too (the Standards re-review of 1cc3db12: a
            # job that finished on its own or failed while being stopped is said so, never simply "stopped").
            stop.work(what, self._stop_outcome(job.task))
            return
        self.stopping.add(job.task)
        stop.late.add(job.task)
        asked = time.monotonic()

        def ended(task: asyncio.Task) -> None:
            self.stopping.discard(task)
            outcome = self._stop_outcome(task)
            with contextlib.suppress(Exception):  # what is logged of it never breaks the loop
                self.log({"kind": "stop-ended-late", "execution": job.execution.id, "operation": job.execution.operation,
                          "implementation": job.execution.requested, "outcome": outcome,
                          "afterS": round(time.monotonic() - asked + STOP_WAIT_S, 2)})
            stop.ended(task, outcome, what)

        job.task.add_done_callback(ended)

    @staticmethod
    def _stop_outcome(task: asyncio.Task) -> str:
        """How work the user stopped ended: "stopped", "completed" (it finished before the Stop reached it: its result
        was not used) or "failed: …"."""
        if task.cancelled():
            return "stopped"
        exc = task.exception()
        if exc is not None:
            return "failed: " + (str(exc) if isinstance(exc, ToolFailed) else type(exc).__name__)[:200]
        return "completed"

    def _finished(self, key, job: Job, task: asyncio.Task) -> None:
        if self.jobs.get(key) is job:
            self.jobs.pop(key, None)
        if task.cancelled() or task.exception() is not None or job.ending():
            return  # a stopped job that finished before its Stop reached it is not kept for reuse either
        # Only small results are kept for reuse here (an outline's polygons). A population's pixels
        # (label maps, masks) are evidence: reused from the evidence store, counted in its allowance,
        # never held in this cache outside the memory bound (contract 1).
        if job.execution.trial is None and job.execution.operation == "outline":
            self.finished[key] = (task.result(), job.execution)
            while len(self.finished) > self.REUSE_KEPT:
                self.finished.popitem(last=False)

    async def _execute(self, op: Operation, impl: Impl, policy: str, capture, prompt: Prompt, params: dict,
                       record: Execution, job: Job) -> dict:
        from .gpu_client import STARTED

        # A GPU worker says when it takes the call from its queue; other work begins at once.
        STARTED.set(lambda: setattr(job, "started", True))
        if impl.worker not in ("interactive", "analysis", "follow", "molmo"):
            job.started = True
        t0 = time.perf_counter()
        with execution.span(f"operation:{op.id}", executionId=record.id, captureId=record.capture,
                            origin=record.origin, policy=policy, selected=impl.id, selection=record.selection,
                            trialId=record.trial):
            execution.note(operation=op.id)
            output: dict = {}
            try:
                from .workspace_study import before_execution

                await before_execution(capture, record)
                output = await self._attempt(op, impl, capture, prompt, params, record, "chosen")
                output = await self._fallbacks(op, impl, policy, capture, prompt, params, record, output)
            finally:
                record.ms = (time.perf_counter() - t0) * 1000
                if op.selected_by:  # each implementation's speed lately, by the setting that chooses it (models_in_use.py)
                    from research_worker.api_usage import note_latency

                    for attempt in record.attempts:
                        ran = op.impl(attempt.impl) if attempt.ok and attempt.ms else None
                        if ran is not None and ran.option:
                            note_latency(f"{op.selected_by}:{ran.option}", attempt.ms)
                if job.stopped:  # it began and the user stopped it: not "never ran", not a failure
                    for attempt in record.attempts:
                        if attempt.not_run == "cancelled":
                            attempt.not_run = "stopped"
                # What produced the kept output: the seed a declared fallback kept, else the last attempt.
                kept = (output.get("seed") or {}).get("producer") if output.get("fellBack") == "empty-refinement" else None
                execution.finish(producer=output.get("producer") or kept or execution.producer(), policy=policy,
                                 fallback=output.get("fellBack"))
                self._record(record, job.ticket, reused=False)
        if record.executed is None:
            failed = [a for a in record.attempts if a.error]
            if failed:
                # It ran and failed (the real error, said as it is); finding nothing is not a failure.
                from .tools import ToolError

                # A refusal in plain words is said as it is; a raw exception becomes a plain line for the user
                # (the partner §1c), the real error staying in the execution record.
                error = failed[-1].error
                if RAW_ERROR.match(error):
                    impl = op.impl(failed[-1].impl)
                    error = f"{impl.label if impl else failed[-1].impl} did not work this time"
                given = failed[-1].next
                raise ToolError(error, next="ask again in a moment" if given is ... else given)
        return output

    async def _attempt(self, op: Operation, impl: Impl, capture, prompt: Prompt, params: dict, record: Execution,
                       why: str) -> dict:
        run = ADAPTERS.get((op.id, impl.id))
        if run is None:
            raise Unavailable(f"{impl.label} has no adapter for {op.title.lower()}", next="name another one, or ask without naming one")
        t0 = time.perf_counter()
        attempt = Attempt(impl.id, False, why=why)
        record.attempts.append(attempt)
        try:
            with execution.span(f"attempt:{impl.id}", selected=impl.id, adapter=f"operations:{op.id}:{impl.id}",
                                model=impl.version or None, provider={"cloud": "cloud", "cpu": "algorithm"}.get(impl.worker, "local"),
                                worker=impl.worker, fallback=None if why == "chosen" else why):
                if params.get("compare_reference") and impl.id != "fiji-analyze-particles":
                    raise ToolFailed("Reference macro comparison requires Analyze Particles (Fiji)",
                                     next="choose Analyze Particles (Fiji) for this run, or turn off reference comparison")
                output = await run(self, capture, prompt, impl, params)
                # A composite says which of its steps produced what it returns.
                execution.finish("completed" if _has_output(output, op) else "empty", producer=output.get("producer"))
        except asyncio.CancelledError:
            # Nobody needs it any more (a queued job is dropped before it begins: contract 4): it never ran.
            attempt.not_run, attempt.ms = "cancelled", (time.perf_counter() - t0) * 1000
            raise
        except Exception as exc:
            attempt.error, attempt.ms = f"{type(exc).__name__}: {exc}" if not isinstance(exc, ToolFailed) else str(exc), \
                (time.perf_counter() - t0) * 1000
            attempt.not_run = not_run_of(exc)
            if isinstance(exc, ToolFailed):
                attempt.next = exc.next
            if isinstance(exc, (Unavailable,)):
                raise
            output = {"polygons": [], "score": 0.0, "failed": True}
            return output
        attempt.ms = (time.perf_counter() - t0) * 1000
        attempt.ok = True
        if output.get("weightsVersion"):  # fine-tuned weights say which version ran (_cells)
            record.version = str(output["weightsVersion"])
        attempt.empty = not _has_output(output, op)
        if not attempt.empty or op.empty_is_result:
            record.executed = output.get("executed") or impl.id
            from . import made_with

            source = output.get("producer")
            # Composite runners return their execution-step receipt id; contributed runners return structured
            # producer metadata. Keep both without treating a receipt string as a mapping.
            producer = dict(source) if isinstance(source, dict) else ({"receipt": source} if source else {})
            actual = op.impl(record.executed)
            record.version = output.get("weightsVersion") or output.get("version") or producer.get("version") or (actual.version if actual else None)
            if not producer.get("catalog"):
                producing = actual.steps[-1] if actual is not None and actual.kind == "composite" else record.executed
                named = made_with.capture_implementation(producing, version=record.version)
                if named:
                    producer["catalog"] = named
            record.producer = producer or None
        return output

    async def _fallbacks(self, op: Operation, impl: Impl, policy: str, capture, prompt: Prompt, params: dict,
                         record: Execution, output: dict) -> dict:
        """Declared fallbacks, for a route only (ADR 0011): never for an explicit raw choice."""
        if policy != "route":
            return output
        for fb in op.fallbacks:
            if impl.id not in fb.after or fb.when == "unavailable":
                continue
            if fb.needs and not os.environ.get(fb.needs):
                continue
            if fb.when == "empty-box" and prompt.kind == "box" and not _has_output(output, op) and not output.get("failed"):
                # Only when it ran and found nothing in the box: a failed attempt (a refusal, an
                # error) is a failure, said as it is (scaffolding review, M1.2 item 1).
                to = op.impl(fb.to)
                if to is None or availability(self.hub, to):
                    continue
                again = await self._attempt(op, to, capture, prompt, params, record, "empty-box")
                if _has_output(again, op):
                    output = {**again, "fellBack": "empty-box",
                              "note": f"{impl.label} found nothing in the box; {to.label} outline, {again.get('ms', 0):.0f} ms"}
            elif fb.when == "empty-refinement" and output.get("seed") and not _has_output(output, op) and not output.get("failed"):
                seed = output["seed"]
                record.attempts.append(Attempt(fb.to, True, ms=0.0, why="empty-refinement (the seed, reused)"))
                record.executed = fb.to
                output = {**seed, "fellBack": "empty-refinement", "seed": seed,
                          "note": output.get("note") or "SAM 3.1 found nothing in EdgeTAM's box; EdgeTAM outline"}
            elif fb.when == "unsure-spot" and prompt.spot is not None and not output.get("failed") \
                    and (not _has_output(output, op) or output.get("score", 1.0) < 0.3):
                to = op.impl(fb.to)
                again = await self._attempt(op, to, capture, prompt, params, record, "unsure-spot")
                output = {**output, **again, "fellBack": "unsure-spot", "note": "spot ring"}
        return output

    # --- recording ----------------------------------------------------------------------------
    def _subject(self) -> str | None:
        from .labels import subject_key

        scene = getattr(self.hub, "scene", None)
        specimen = (getattr(scene, "context", {}) or {}).get("specimen") if scene is not None else None
        return subject_key(specimen) if specimen else None

    def _record(self, record: Execution, ticket, reused: bool) -> Execution:
        self.executions[record.id] = record
        while len(self.executions) > 512:
            self.executions.popitem(last=False)
        try:
            # The day's interaction log keeps every execution's outcome (Amendment 1, item 4). An
            # arena trial's line is blind until its vote (which implementation ran is left out);
            # `reveal` logs it in full once the vote is in.
            wire = record.wire() if record.trial is None else _blind(record.wire())
            self.log({"kind": "execution", **wire, "reused": reused,
                      "ticket": getattr(ticket, "id", None), "trial": record.trial})
        except Exception:
            pass
        first = record.made_by().get("fallbackFrom")
        if first and not reused and record.trial is None:
            # The default did not run and another did in its place: said in the meter's panel (fallbacks.py).
            from . import fallbacks
            from .names import said_impl

            tried = record.attempts[0]
            why = (f"{said_impl(first)} {'found nothing' if tried.empty else 'could not run'}"
                   + (f": {tried.error[:100]}" if tried.error else f" ({tried.not_run})" if tried.not_run else ""))
            fallbacks.of(self.hub).note(record.operation, said_impl(first), said_impl(record.executed), why)
        return record

    def kept_use(self, operation: str, execution_id: str | None, ticket=...) -> None:
        """A use of a result kept as evidence (a population or a column group made earlier from the same inputs),
        recorded as a reuse of the execution that made it, never as a fresh run (invariant 5)."""
        from . import ticket as tickets
        from .brain.registry import attach

        ticket = tickets.current() if ticket is ... else ticket
        if execution_id:
            attach(execution=execution_id)
        try:
            self.log({"kind": "execution-use", "execution": execution_id, "operation": operation, "reused": True,
                      "ticket": getattr(ticket, "id", None), "from": "evidence"})
        except Exception:
            pass

    def _use(self, record: Execution, ticket, reused: bool) -> None:
        """Each request's use of a job's result, the request that started it included (invariant 5)."""
        from .brain.registry import attach

        attach(execution=record.id)  # the tool run that used it names it (the index's result entries)
        try:
            self.log({"kind": "execution-use", "execution": record.id, "operation": record.operation, "reused": reused,
                      "ticket": getattr(ticket, "id", None)})
        except Exception:
            pass

    def background(self, operation: str, implementation: str, capture: int | None, ms: float = 0.0) -> Execution:
        """The execution of a background run a request has just used (contract 8: background results
        become evidence only then), so what it became is linked to an execution like any other
        (Amendment 4, item 3). It ran on its own loop, by the defaults: origin background, policy route."""
        from .ticket import current

        record = Execution(operation, implementation, "route", "background", capture, "settings")
        record.attempts.append(Attempt(implementation, True, ms=ms, why="chosen"))
        record.executed, record.ms = implementation, ms
        request = current()
        self._record(record, request, reused=False)
        self._use(record, request, reused=False)
        return record

    def produced(self, record: Execution, output_id: str) -> None:
        """What an execution's result became (an outline, a population), linked to it in the log as it
        is made (Amendment 1, item 4: outcomes are linkable)."""
        record.outputs.append(output_id)
        try:
            self.log({"kind": "execution-output", "execution": record.id, "operation": record.operation,
                      "output": output_id, "trial": record.trial})
        except Exception:
            pass

    def reveal(self, execution_id: str | None) -> Execution | None:
        """After an arena vote: the trial's execution logged in full."""
        record = self.get(execution_id)
        if record is not None:
            record.revealed = True
            try:
                self.log({"kind": "execution-revealed", **record.wire(), "trial": record.trial})
            except Exception:
                pass
        return record

    def fell_back(self, operation: str, from_impl: str, to_impl: str, capture, why: str,
                  origin: str = "brain-default") -> Execution:
        """A declared fallback a caller carried out itself (find-like: the finder's vision boxes when
        SAM 3.1 can't run here), recorded as the operation's execution."""
        record = Execution(operation, from_impl, "route", origin, getattr(capture, "id", None), "fixed",
                           attempts=[Attempt(from_impl, False, error=why, why="chosen", not_run="unavailable"),
                                     Attempt(to_impl, True, why="unavailable")],
                           executed=to_impl, subject=self._subject(), actor=_actor_now(None))
        return self._record(record, None, reused=False)

    def get(self, execution_id: str | None) -> Execution | None:
        return self.executions.get(execution_id) if execution_id else None


def _request_choice(ticket, operation: str, from_context: bool = True) -> dict | None:
    """The request's choice for `operation` (`use`, contract 7), seen only by work done for the request:
    work started as not for it (options.unrelated) clears the request's choices, and so sees none. A
    ticket a caller passes on purpose is that request's."""
    from .options import overriding

    if ticket is None or (from_context and overriding() is None):
        return None
    return (getattr(ticket, "choices", {}) or {}).get(operation) or None


def alternatives_text(hub, operation: str, prompt: str | None = None) -> str:
    """What can do `operation` here and now, without running anything (M1.4, contract 6): each
    implementation that takes `prompt`, where it runs, whether it is the default and the declared
    fallbacks of a route, and whether it can run now or why not."""
    from .brain.registry import ToolFailed
    from .options import choices_of

    op = OPERATIONS.get(operation)
    if op is None:
        raise ToolFailed(f"there is no operation {operation!r}",
                         next="ask about outlining, segmenting every cell or finding every one like it")
    with choices_of(None):  # the default, not a choice this request made
        default = getattr(hub.settings, op.selected_by, None) if op.selected_by else None
    where = {"interactive": "on this Mac", "analysis": "on this Mac", "follow": "on this Mac", "molmo": "on this Mac",
             "cloud": "in the cloud", "cpu": "app code"}
    lines = [f"{op.title} ({op.id}), returns {op.returns}" + (f", for a {prompt}" if prompt else "") + ":"]
    for impl in op.implementations:
        if prompt and prompt not in impl.accepts:
            continue
        if impl.option is None and op.selected_by:
            continue  # a declared fallback only (the spot ring): listed under the routes
        why = availability(hub, impl)
        tags = [where.get(impl.worker, impl.worker), "takes " + ", ".join(impl.accepts)]
        if impl.option is not None and impl.option == default:
            tags.append("the default")
        if impl.worker == "cpu" and impl.kind == "model":
            tags[0] = "on this Mac (its processor)"  # a model, not app code (the M3.5 review)
        line = f"- {impl.label} [{impl.id}]: {'; '.join(tags)}; " + ("can run now" if not why else f"can't run here ({why})")
        if not why and impl.kind == "model" and impl.worker in ("interactive", "analysis", "follow", "molmo") \
                and getattr(hub, "loads", None) is not None:
            # Whether it is loaded, and what a load costs here (loads.py): a cold model is a wait the models are told.
            client = worker_client(hub, impl.worker, start=False, model=LOAD_KEYS.get(impl.id, impl.id))
            if client is not None:
                line += "; " + hub.loads.state_words(client, LOAD_KEYS.get(impl.id, impl.id))
        if impl.option is not None and op.selected_by:
            from .model_checks import checks_of, said
            from .names import said_impl

            store = checks_of(hub)  # its last check, only when it failed (model_checks.py, Slice 8)
            failed = said(store.of(op.selected_by, impl.option), said_impl(impl.id), store.version) if store is not None else ""
            if failed:
                line += f"; {failed}"
        if impl.note:
            line += f"; {impl.note}"
        lines.append(line)
    routes = [f"after {', '.join(f.after)}: {f.when} -> {f.to}" + (f" (needs {f.needs})" if f.needs else "")
              for f in op.fallbacks]
    if routes:
        lines.append("declared fallbacks (a route only; an explicit choice runs raw): " + "; ".join(routes))
    lines.append("Nothing was run. `use` picks one for this request; `set_default` changes the default.")
    return "\n".join(lines)


def _blind(wire: dict) -> dict:
    """An arena arm's execution as the log keeps it before the vote: no implementation named."""
    # Error texts can name the model ("facebook/sam2.1-hiera-tiny out of memory"): only that it failed.
    return {**{k: v for k, v in wire.items() if k not in ("producer", "version")},
            "requested": "arm", "executed": "arm" if wire.get("executed") else None,
            "attempts": [{**a, "impl": "arm", **({"error": "failed"} if a.get("error") else {})}
                         for a in wire.get("attempts", [])]}


def _has_output(output: dict, op: Operation | None = None) -> bool:
    """Whether a run made anything: any of its operation's output keys holds something (review B2: looks,
    groups, light, names and your labels are recorded as made, not "empty", when they made them)."""
    return any(output.get(key) for key in (op.output if op is not None else ("polygons", "matches")))


def runner(hub) -> Runner:
    """The hub's runner (made on first use, so a test's stand-in hub gets one too)."""
    found = getattr(hub, "runner", None)
    if not isinstance(found, Runner):
        found = Runner(hub)
        try:
            hub.runner = found
        except AttributeError:
            pass
    return found


# --- adapters ---------------------------------------------------------------------------------
CLICK_PRIORITY = "click"


@adapter("link", "trackastra")
async def _trackastra(run: "Runner", capture, prompt: "Prompt", impl: Impl, params: dict) -> dict:
    """Trackastra on two populations of the same specimen (params "a" and "b": population ids, "a" made first):
    their exclusive label maps and grey views as two frames of one "video", linked greedily. Links are between
    members, each with Trackastra's own score, of kind same-object (Trackastra's guess); nothing is linked that it
    did not link. The two views must be related by the slide's move alone (contract 3's preconditions: one motion
    epoch, the same picture size, eyepiece scale and objective), and are compared where they overlap, the second
    placed by the slide's measured move, once their pictures are measured to line up there (`line_up`, within
    LINE_UP_PX); otherwise it can't tell, and says why (ADR 0012). The masks are read for
    the maps without being kept, the decodes are counted (contract 1), and the population is never changed: its
    members are taken on the loop, and the rest runs in a thread (the M3.5 review)."""
    import asyncio as _asyncio

    from .capture import reserving
    from .evidence import evidence_of, label_map
    from .licences import adapter as gated

    store = evidence_of(run.hub)
    a, b = store.get(params.get("a")), store.get(params.get("b"))
    if a is None or b is None:
        raise ToolFailed("two populations kept today are needed to link", next="outline the cells in both views first")
    links = _rejoins_for(run.hub, a, b)
    from .population import _epoch_now

    now = _epoch_now(run.hub)
    why = _not_linkable(a, b, links, now)
    if why:
        raise ToolFailed(f"can't tell which cells of {a.id} and {b.id} are the same: {why}",
                         next="outline the cells in both views while the slide's movement is followed")
    from .population import _ViewOf

    va, vb = _ViewOf(a, links, now), _ViewOf(b, links, now)  # offsets in the epoch a verified rejoin carried them to
    sx, sy = int(round(vb.dx - va.dx)), int(round(vb.dy - va.dy))
    w, h = int(a.view["width"]), int(a.view["height"])
    x0, y0, x1, y1 = max(0, -sx), max(0, -sy), min(w, w - sx), min(h, h - sy)
    if x1 - x0 < 32 or y1 - y0 < 32:
        raise ToolFailed(f"can't tell which cells of {a.id} and {b.id} are the same: the two views do not overlap",
                         next=None)  # no next: they show different places on the slide
    # Each eyepiece circle in its own cut's px (the second's cut starts at the first's moved by the slide's move).
    discs = [(d[0] - ox, d[1] - oy, d[2]) if d else None
             for d, (ox, oy) in ((a.view.get("disc"), (x0, y0)), (b.view.get("disc"), (x0 + sx, y0 + sy)))]
    # On the loop: what each population is now (a correction may change it while the thread works).
    taken = [(p, [(m.id, m.mask, float(m.extras.get("score") or 0.0)) for m in p.live()], store.capture_jpeg(p))
             for p in (a, b)]

    def work() -> dict:
        import cv2
        import numpy as np

        views, maps, ids = [], [], []
        cuts = ((slice(y0, y1), slice(x0, x1)), (slice(y0 + sy, y1 + sy), slice(x0 + sx, x1 + sx)))
        with reserving(run.hub, w, h, "link", owner="linking two views"):  # one footprint, measured (the re-check)
            for (p, members, jpeg), cut in zip(taken, cuts):
                gray = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE) if jpeg else None
                labels = label_map(p.view, members, p.path, p.overlapping, len(p.members), complete=True)
                if gray is None:
                    raise InputUnread(f"can't tell which cells are the same: {p.title}'s view can't be read now",
                                     next="ask again in a moment")
                if labels is None:
                    raise InputUnread(f"can't tell which cells are the same: {p.title}'s masks could not be read back",
                                     next="outline the cells again")
                views.append(gray[cut].astype(np.float32))  # cut to the overlap at once: the full view goes
                maps.append(labels[cut].astype(np.int32))
                ids.append({n: mid for n, (mid, _, _) in enumerate(members, start=1)})
                del gray, labels
            # The two cuts must show the same place: nothing else checks the offsets they were placed by (the K2
            # re-check, note 1: offsets 80 px wrong once kept two wrong links). Measured before the model is asked.
            lined = line_up(views[0], views[1], discs, w)
            if isinstance(lined, str):
                raise ToolFailed(f"can't tell which cells of {a.id} and {b.id} are the same: {lined}",
                                 next="outline the cells in both views while the slide's movement is followed")
            stack, label_stack = np.stack(views), np.stack(maps)
            # The cells each view has inside the overlap: only those were compared (the others are can't tell, never
            # "not the same": ADR 0012; the second re-check).
            present = [{int(n) for n in np.unique(m) if n} for m in maps]
            del views, maps
            module = gated("trackastra")
            edges = module.link(stack, label_stack)
            producer = {"implementation": "trackastra", "weights": module.TRACKASTRA_MODEL, "mode": "greedy_nodiv",
                        "version": module.installed_version()}  # what is installed, never the pinned number said
        candidates = [(ids[0][l0], ids[1][l1], score) for t0, l0, t1, l1, score in edges
                      if t0 == 0 and t1 == 1 and l0 in ids[0] and l1 in ids[1]]
        compared = {"a": sum(1 for n in present[0] if n in ids[0]), "b": sum(1 for n in present[1] if n in ids[1])}
        return {**one_to_one(candidates), "overlap": [x0, y0, x1, y1], "shift": [sx, sy], "compared": compared,
                "lineUp": lined, "producer": producer}

    return await _asyncio.to_thread(work)


def one_to_one(candidates) -> dict:
    """A linking model's (from, to, score) candidates kept one to one only: a cell linked to two (a division, or two
    candidates) is not the same object as either, nor are two cells linked to one (a merge). Both ends of every edge
    not kept are named (`ambiguous`), with the edges (`ambiguousEdges`). Holds whatever the model returns (Trackastra
    runs without divisions; M3.5's re-checks)."""
    from collections import Counter

    outs, ins = Counter(a for a, _, _ in candidates), Counter(b for _, b, _ in candidates)
    links = [{"from": a, "to": b, "score": score, "kind": "same-object", "basis": "trackastra"}
             for a, b, score in candidates if outs[a] == 1 and ins[b] == 1]
    edges_unsure = [{"from": a, "to": b, "score": score} for a, b, score in candidates if outs[a] > 1 or ins[b] > 1]
    unsure = sorted({e["from"] for e in edges_unsure} | {e["to"] for e in edges_unsure})
    return {"links": links, "ambiguous": unsure, "ambiguousEdges": edges_unsure}


# Two views given to the linking model must line up where the slide's measured move puts them: within LINE_UP_PX
# (the residual a tracker tolerates, as the hub's largest drift correction), by a phase correlation of the part both
# eyepiece circles show, measured as the motion tracker measures a move (motion.py: LINE_UP_WIDTH px wide, slow light
# removed, a Hanning window) and trusted only at its keyframe bar (LINE_UP_RESPONSE, motion's refresh_response).
LINE_UP_PX = 10.0
LINE_UP_WIDTH = 512
LINE_UP_RESPONSE = 0.18
LINE_UP_MIN = 32  # px of the measured patch on its shorter side, else too little to tell


def line_up(a, b, discs, frame_width: int) -> dict | str:
    """How far the second cut view's picture is from the first's, where the slide's measured move put it:
    {"residual": [dx, dy] (px), "response"} when they line up, else why not, in words. Only the part inside both
    eyepiece circles (`discs`: each (cx, cy, r) in its cut's px, or None) is compared: the circle's own edge stays
    put in the frame while the slide moves, so it must not be measured."""
    import math

    import cv2

    from .motion import phase_correlate

    h, w = a.shape[:2]
    x0, y0, x1, y1 = 0.0, 0.0, float(w), float(h)
    for disc in discs:
        if disc:
            cx, cy, r = (float(v) for v in disc)
            half = r / math.sqrt(2.0)  # the square inside the circle
            x0, y0, x1, y1 = max(x0, cx - half), max(y0, cy - half), min(x1, cx + half), min(y1, cy + half)
    x0, y0, x1, y1 = int(math.ceil(x0)), int(math.ceil(y0)), int(x1), int(y1)
    scale = min(1.0, LINE_UP_WIDTH / float(frame_width or w))
    if min(x1 - x0, y1 - y0) * scale < LINE_UP_MIN:
        return "the part of the slide both views show is too small to check that they line up"

    def optimal(n: int) -> int:  # the largest even DFT-optimal size up to n: others come out half a pixel off
        while n > 2 and (n % 2 or cv2.getOptimalDFTSize(n) != n):
            n -= 1
        return n

    def patch(view):
        p = view[y0:y1, x0:x1]
        p = cv2.resize(p, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1.0 else p.copy()
        ph, pw = optimal(p.shape[0]), optimal(p.shape[1])
        top, left = (p.shape[0] - ph) // 2, (p.shape[1] - pw) // 2
        p = p[top:top + ph, left:left + pw]
        return p - cv2.GaussianBlur(p, (0, 0), 8)

    pa, pb = patch(a), patch(b)
    window = cv2.createHanningWindow((pa.shape[1], pa.shape[0]), cv2.CV_32F)
    (px, py), response = phase_correlate(pa, pb, window)
    dx, dy = px / scale, py / scale
    off = math.hypot(dx, dy)
    if response < LINE_UP_RESPONSE:
        return ("whether the two views line up where the slide's measured move puts them can't be checked (their "
                f"pictures match too weakly there: {response:.2f})")
    if off > LINE_UP_PX:
        shown = f"{off:.1f}" if off < 20 else f"{off:.0f}"  # tenths near the limit, so just past it never reads as at it
        return f"the two views do not line up where the slide's measured move puts them (off by {shown} px)"
    return {"residual": [round(dx, 1), round(dy, 1)], "response": round(float(response), 3)}


def _rejoins_for(hub, a, b):
    """This run's verified rejoins (hub.epoch_links), only for two views this run made: epoch numbers start again
    with every run, so another run's views are read by their own numbers alone (the second re-check: this run's
    rejoin once joined two epochs of an earlier run)."""
    from .capture import RUN

    if a.view.get("run") == RUN and b.view.get("run") == RUN:
        return getattr(hub, "epoch_links", None)
    return None


def _not_linkable(a, b, links=None, now=None) -> str:
    """Why two populations' views cannot be related by the slide's move alone, or "". Read as the cells' layer reads a
    view (population._ViewOf: in the epoch a verified rejoin carried it to, its measured eyepiece radius), held to
    apply.preconditions, and never across a restart of the app: epoch numbers start again with every run (M3.5's
    re-check; a view with no run recorded is never linked: which run made it is not known)."""
    from .apply import preconditions
    from .population import _ViewOf

    if a.view.get("run") is None or b.view.get("run") is None:
        return ("it is not known which run of the app made one of the views, so how the slide moved between them is "
                "not known")
    if a.view.get("run") != b.view.get("run"):
        return "the app restarted between the two views, so how the slide moved between them is not known"
    if a.view.get("tracked") is False or b.view.get("tracked") is False:
        return "the slide's motion was not registered on one of the views"
    if a.view.get("dx") is None or b.view.get("dx") is None:
        return "where one of the views was on the slide is not known"
    return preconditions(_ViewOf(a, links, now), _ViewOf(b, links, now), (b.scale_id or {}).get("objective")) or ""


@adapter("outline", "edgetam", "sam2.1-tiny", "sam3.1-local", "micro-sam-lm")
async def _segment_on_worker(run: Runner, capture, prompt: Prompt, impl: Impl, params: dict) -> dict:
    client = await started(worker_client(run.hub, impl.worker, model=impl.id))
    payload = {"frame_id": capture.id, "jpeg": capture.jpeg, "model": impl.id}
    if prompt.box is not None:
        payload["box"] = [float(v) for v in prompt.box]
    else:
        payload["points"] = [[float(prompt.x), float(prompt.y)]]
        payload["prefer_small"] = prompt.prefer_small
    if impl.id == "sam3.1-local" and prompt.text and prompt.kind == "text":
        # Words alone go to SAM 3.1's detector; a box or a point is outlined by its tracker, the one thing it holds
        # (gpu_worker/worker.py segment_sam31): its name would turn it into "find what is like this".
        payload["text"] = prompt.text
    # A model's load does not count against this: the worker says when it loads (SAM 3.1's 3.5 GB, micro-sam's own
    # Python under its STARTUP_S) and the client moves the deadline by the load (gpu_client.py). This is for the run.
    timeout = 180.0 if impl.worker == "analysis" else 30.0
    return await client.call("segment", timeout=timeout, priority=params.get("priority", "interactive"), **payload)


@adapter("outline", "sam3.1-fal")
async def _segment_fal(run: Runner, capture, prompt: Prompt, impl: Impl, params: dict) -> dict:
    from . import tools

    return await tools._segment_fal(run.hub, capture, capture.jpeg, prompt.x or 0.0, prompt.y or 0.0, prompt.box, prompt.text)


@adapter("outline", "edgetam>sam3.1-fal", "edgetam>sam3.1-local")
async def _seeded(run: Runner, capture, prompt: Prompt, impl: Impl, params: dict) -> dict:
    """EdgeTAM finds how far the clicked thing extends, then SAM 3.1 redraws it from that box. A
    box already says how big the thing is: it goes straight to SAM 3.1. An empty refinement returns
    nothing here; the seed rides along so a route's declared fallback can use it."""
    from .tools import SEED_PAD, padded_bbox

    first, then = (OPERATIONS["outline"].impl(s) for s in impl.steps)

    async def step(part: Impl, ask: Prompt) -> tuple[dict, str | None]:
        with execution.span(f"step:{part.id}", selected=part.id, model=part.version or None,
                            provider="cloud" if part.worker == "cloud" else "local") as scope:
            out = await ADAPTERS[("outline", part.id)](run, capture, ask, part, params)
            execution.finish("completed" if out.get("polygons") else "empty")
        return out, getattr(scope, "id", None)

    if prompt.box is not None:  # a box already says how big the thing is: no seed
        out, made = await step(then, prompt)
        execution.note(policy="supplied-box-skips-seed")
        return {**out, "executed": impl.id, "producer": made}
    seed, seeded = await step(first, prompt)
    if not seed.get("polygons"):
        return {**seed, "polygons": [], "producer": seeded}
    box = padded_bbox(seed["polygons"], SEED_PAD)
    refined, made = await step(then, Prompt("box", box=box, text=prompt.text))
    ms = seed.get("ms", 0.0) + refined.get("ms", 0.0)
    where = "fal" if then.id.endswith("fal") else "this Mac"
    if not refined.get("polygons"):
        return {"polygons": [], "score": 0.0, "ms": ms, "seed": {**seed, "ms": ms, "producer": seeded},
                "note": f"SAM 3.1 ({where}) found nothing in EdgeTAM's box; EdgeTAM outline, {ms:.0f} ms"}
    return {**refined, "ms": ms, "executed": impl.id, "producer": made,
            "note": f"EdgeTAM box, SAM 3.1 ({where}) outline, score {refined.get('score', 0):.2f}, {ms:.0f} ms"}


@adapter("outline", "spot-ring")
async def _spot_ring(run: Runner, capture, prompt: Prompt, impl: Impl, params: dict) -> dict:
    import numpy as np

    spot = prompt.spot
    r = max(float(spot.radius) * 1.3, 6.0)
    x, y = float(prompt.x), float(prompt.y)
    ring = [[x + r * np.cos(a), y + r * np.sin(a)] for a in np.linspace(0, 2 * np.pi, 24, endpoint=False)]
    return {"polygons": [ring], "score": 0.0, "ms": 0.0}


@adapter("cells", "cellpose-sam", "micro-sam-lm", "cellpose-sam-tuned")
async def _cells(run: Runner, capture, prompt: Prompt, impl: Impl, params: dict) -> dict:
    from .source_images import worker_input

    client = await started(worker_client(run.hub, impl.worker, model=impl.id))
    tuned = {}
    if impl.weights:  # the version the job was keyed with (Runner.run), named in the call and in what it made
        from . import finetuned

        wanted = params.get("weightsVersion")
        found = next((v for v in finetuned.versions(impl.weights) if v["whole"] and v["version"] == wanted), None) \
            if wanted else finetuned.current(impl.weights)
        if found is None:
            raise Unavailable(f"{impl.label} can't run here ({finetuned.missing(impl.weights)})",
                              next=None)  # no next: its weights are made outside the app (make finetune-cells, from corrected cells)
        tuned = {"weights": found["weights"], "version": found["version"]}
    out = await client.call("cells", timeout=180, priority=params.get("priority", "background"), frame_id=capture.id, jpeg=await worker_input(capture),
                            crop=params.get("crop"), model=impl.id, **tuned)
    return {**out, "version": f"{impl.version} {tuned['version']}", "weightsVersion": tuned["version"]} if tuned else out


@adapter("cells", "cellpose-file")
async def _cells_yours(run: Runner, capture, prompt: Prompt, impl: Impl, params: dict) -> dict:
    """A Cellpose model of the user's own (mine.py; ADR 0021), only while it is turned on: Cellpose's own code runs its
    checked weights from their read-only revision folder, in a GPU worker of its own (its entry's host "own"), never
    the live view's."""
    from gpu_worker import manifests as catalog
    from gpu_worker import model_files

    from . import mine
    from .source_images import worker_input

    m = mine.entry_of(impl.id)
    if m is None or not mine.state(m)["on"]:
        raise Unavailable(f"{impl.label} is not turned on", next="turn it on on the Models page, which checks it first")
    weights = model_files.revision_dir(m) / model_files.files(m)[0]["path"]
    client = await started(worker_client(run.hub, impl.worker, model=m.key))
    out = await client.call("cells", timeout=180, priority=params.get("priority", "background"), frame_id=capture.id,
                            jpeg=await worker_input(capture), crop=params.get("crop"), model="cellpose-file", weights=str(weights),
                            version=catalog.short_revision(m))
    return {**out, "version": impl.version}


@adapter("cells", "basic-bright-objects")
async def _bright_objects(run: Runner, capture, prompt: Prompt, impl: Impl, params: dict) -> dict:
    from . import bright_objects, owned_process
    from .capture import captures

    # Decode, smooth, label and compress within the shared working-memory allowance.
    with captures(run.hub).memory.reserve("analysis", capture.width * capture.height * 48,
                                         "finding bright regions on the original picture"):
        return await owned_process.off_loop(bright_objects.analyze, capture, params.get("crop"), impl.version,
                                            _declared_values(impl.id, params))


def _declared_values(entry: str, params: dict) -> dict:
    """A bundled algorithm's settings for one run (catalog format 2): those the door checked and passed (`inputs`), else
    its manifest's defaults, checked once more here."""
    import json

    from gpu_worker import manifests as catalog

    m = catalog.get(entry)
    if m is None or not m.contract:
        return {}
    given = json.loads(params["inputs"]) if params.get("inputs") else {}
    values, problems = catalog.params_given(m, {k: v for k, v in given.items() if k not in catalog.HIDDEN})
    if problems:
        raise Unavailable(f"{m.name}: " + "; ".join(problems), next=None)  # no next: the words say which value
    return values


@adapter("cells", "fiji-analyze-particles")
async def _fiji_particles(run: Runner, capture, prompt: Prompt, impl: Impl, params: dict) -> dict:
    """ImageJ's Analyze Particles in the managed Fiji's generation in use (fiji_bindings.py), on the camera's picture
    (or its crop), its particles as polygons in frame px: particles, never called cells by the app."""
    import tempfile
    from pathlib import Path

    from . import fiji_bindings, fiji_generations, owned_process

    import hashlib
    import json

    n = fiji_generations.current()
    receipt = fiji_generations.receipt(n) if n is not None else None
    if not receipt or fiji_generations.proven(impl.id, n=n):
        raise Unavailable(f"{impl.label} is not proved on the active Fiji generation",
                          next="open its card on the Models page and choose Prove it")

    def run_it() -> dict:
        import cv2
        import numpy as np

        data = capture.analysis_bytes
        picture = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        source_hash = hashlib.sha256(data).hexdigest()
        pixel_hash = hashlib.sha256(picture.tobytes()).hexdigest()
        x0 = y0 = 0
        crop = params.get("crop")
        if crop:
            x0, y0, width, height = (int(v) for v in crop)
            picture = picture[y0:y0 + height, x0:x0 + width]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "view.png"
            if not cv2.imwrite(str(path), picture):
                raise fiji_generations.Failed("Fiji's input image could not be written")
            out = fiji_bindings.analyze_particles(path, n=n)
            if (out["width"], out["height"]) != (picture.shape[1], picture.shape[0]):
                raise fiji_generations.Failed("Fiji returned results for different image dimensions")
            producer = {"implementation": impl.id,
                        "input": {"capture": capture.id, "sha256": source_hash, "pixelsSha256": pixel_hash,
                                  "executionSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                  "width": picture.shape[1], "height": picture.shape[0],
                                  "sourceImage": capture.source_image},
                        "settings": {"conversion": "8-bit", "thresholdMethod": "Default", "minimumSizePx2": fiji_bindings.MIN_SIZE},
                        "runtime": {"generation": n, **out.get("runtime", {}), "base": receipt["base"],
                                    "receiptSha256": hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest(),
                                    "proof": receipt["proofs"][impl.id], "files": receipt.get("files", []),
                                    "bindingSha256": hashlib.sha256(fiji_bindings.SERVER.read_bytes()).hexdigest()},
                        "threshold": out.get("threshold"), "polygons": [p["polygon"] for p in out["particles"]],
                        "maskContract": out["maskContract"], "particles": out["particles"],
                        "nativeMeasurements": {"grid": "input crop pixels", "origin": [x0, y0],
                            "area": "Exact occupied native mask pixels, px²",
                            "centroid": "Native mask pixel centers (index + 0.5), px from crop origin",
                            "mean": "ImageJ mean intensity on its 8-bit analysis input, before particle fill"}}
            if params.get("compare_reference"):
                producer["comparison"] = fiji_bindings.compare(path, out)
            out["producer"] = producer
            return out, x0, y0

    try:
        out, x0, y0 = await owned_process.off_loop(run_it)
    except (fiji_generations.Failed, OSError, ValueError, TypeError) as exc:
        raise ToolFailed(f"Analyze Particles failed: {exc}",
                         next="retry this action; if it fails again, open Fiji on the Models page and Prove it") from None
    polygons = [[[x + x0, y + y0] for x, y in p["polygon"]] for p in out["particles"]]
    return {"polygons": polygons, "labels": {**out["labels"], "origin": [x0, y0]},
            "version": f"{impl.version}, Fiji generation {out['generation']}",
            "producer": out["producer"], "threshold": out.get("threshold")}


@adapter("cells", "edgetam-automask")
async def _automask(run: Runner, capture, prompt: Prompt, impl: Impl, params: dict) -> dict:
    """EdgeTAM's automatic masks of the disc (gpu_worker/automask.py), each mask as it made it."""
    from .source_images import worker_input

    client = await started(worker_client(run.hub, impl.worker))
    disc = params.get("disc")
    out = await client.call("automask", timeout=60, priority=params.get("priority", "background"), frame_id=capture.id, jpeg=await worker_input(capture),
                            disc=list(disc) if disc else None, points=[])
    return {**out, "polygons": [p for r in out.get("regions") or [] for p in r.get("polygons", [])[:1]]}


@adapter("find-like", "sam3.1-all")
async def _matches(run: Runner, capture, prompt: Prompt, impl: Impl, params: dict) -> dict:
    from .fal_segment import segment_all as fal_all
    from gpu_worker.worker import mask_to_polygons

    found = await fal_all(capture.jpeg, capture.width, capture.height, box=prompt.box, text=prompt.text)
    return {"matches": [{"polygons": mask_to_polygons(f["mask"])[:3], "score": f["score"]} for f in found],
            "masks": [{"mask": f["mask"], "score": f["score"]} for f in found],  # each match's own mask (evidence)
            "polygons": [p for f in found for p in mask_to_polygons(f["mask"])[:1]]}


@adapter("motion", "spots")
async def _motion(run: Runner, capture, prompt: Prompt, impl: Impl, params: dict) -> dict:
    """What moves in the clip's frames (movement.py), each decoded when it is reached, one at a time
    (motiontracks.measure_clip), with the last frame's picture of the tracks when asked for."""
    from .motiontracks import measure_clip, reading_motion_frames

    return await reading_motion_frames(prompt.clip, lambda: measure_clip(
        run.hub, list(prompt.clip or ()), params.get("umPerPx"), params.get("scaleWords") or "",
        bool(params.get("draw")), str(params.get("cls") or "analysis")))  # a researcher's own run: "research"


def label_of(impl_id: str | None) -> str:
    """An implementation's label (what the user sees), from any operation; the id when it is none of them."""
    for op in OPERATIONS.values():
        found = op.impl(impl_id) if impl_id else None
        if found is not None:
            return found.label
    return impl_id or "an unknown model"


def _enrichments() -> None:
    from . import enrichment

    enrichment.register()  # their adapters (session knowledge K1)


_enrichments()
