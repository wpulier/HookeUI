"""Missions (the partner's Amendment 1 B): a researcher with a plan the app runs on a schedule, for longer than
one model run ("count them every 10 minutes for the next half hour").

A mission is not a model running for hours. The app runs the planned operation at each due time (a slot) through
the researcher's hands (research/hands.py: the "research" class, made for the researcher, never shown), and writes
one ledger row per slot, once, as it happens. The researcher's model is called at checkpoints to read what the slots
made (a short "continue" run), and once at the end to write its finding (P2c-2).

The rules kept here:
- **One row per slot, written once, with its real time.** A slot that can't start on time runs late, with its real
  time, or is missed after the grace (half its period, at most 2 minutes); a missed slot stays missed. Nothing is
  backdated or invented.
- **The same field throughout.** The field is pinned at the start (the view's epoch, slide offset, picture size,
  eyepiece scale, objective and the app's run). A slot whose view is not that field is "not comparable", said why,
  and its operation does not run: never measured as the same field.
- **Slots never stack.** A mission's slots run one after another; one research operation runs at a time across the
  app (the hands' own slot), so a slot that finds it taken waits, within its grace.
- **Live first.** Every operation is the researcher's: the user's work goes first (§1g).
- **It lives in the app process.** It survives a browser closing, not an app restart: it comes back "interrupted"
  with its ledger, and carrying it on marks the slots due while the app was down "missed: the app was not running".
- **It belongs to its researcher's session** (ADR 0017). Midnight does nothing to it; while another session is open
  its slots are "not comparable" (the live view is that session's), and they run again when its session is opened.
"""
from __future__ import annotations

import asyncio
import copy
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

from ..apply import GEOMETRY_SHARE

MISSION_MAX_S = 4 * 3600.0  # the longest window for now; raised once measured (the partner's Amendment 1 B)
MIN_EVERY_S = 60.0  # the shortest period between slots
ACQUISITION_MIN_EVERY_S = 30.0
GRACE_MAX_S = 120.0  # how late a slot may still run
BUSY_RETRY_S = 2.0  # a slot that finds the app's research operation taken asks again this often, within its grace
SLIDE_SHARE = 0.1  # the slide moved more than this share of the eyepiece's radius since the pin: another field
OUTCOMES = ("done", "failed", "missed", "not comparable")
# How its slots are counted (the playbook's section 14): a baseline at the start, on the field just pinned, then one
# every period to the end of the window ("t0+": 3 h every 10 min is 19 slots). Rows are written once each, as each
# slot happens, never created ahead of it; `wire` gives the plan's count and the next slot's time.
CONVENTION = "t0+"
STATUSES = ("watching", "checkpoint", "finishing", "done", "cancelled", "interrupted", "failed")


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


@dataclass
class Slot:
    """One ledger row: a due time and what happened then, written once."""
    n: int
    due: float  # wall clock
    operation: str
    outcome: str = ""  # done | failed | missed | not comparable ("" until it happened)
    why: str = ""
    ran_at: float | None = None  # when it ran (wall), late or not
    entry: str | None = None  # what it made in the session's index (e…, m…, d…)
    count: int | None = None
    capture: dict | None = None  # {"frame", "seq", "boot"} of the view it ran on

    def row(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, "")}


@dataclass
class Mission:
    rid: str
    operation: str  # what each slot runs (hands: segment, enhance, motion)
    every_s: float
    window_s: float
    started_at: float  # wall clock: slot n is due at started_at + n * every_s, slot 0 at the start (CONVENTION)
    pinned: dict  # the field: population.view_facts of the view at the start (epoch, dx, dy, width, height,
    # discPx, run...) and the objective then
    checkpoint_every: int = 3  # done slots between checkpoints (P2c-2)
    status: str = "watching"
    ledger: list[Slot] = field(default_factory=list)
    why_ended: str = ""
    origin: dict = field(default_factory=dict)  # conversation/delegation that requested this mission
    id: str = field(default_factory=lambda: str(uuid.uuid4()))  # this occurrence, independent of its wall clock
    notes_from: int | None = None  # oldest idle steering note not handed to a worker yet
    authority: dict = field(default_factory=dict)  # validated intent, scoped to this occurrence/effort/deliverable
    model_runs: list[dict] = field(default_factory=list)  # app-owned queued, dispatched and terminal run facts
    acquisition: dict = field(default_factory=dict)  # literal upcoming camera interval, independent of model runs

    @property
    def slots(self) -> int:
        """Its slots: one at the start (the baseline, on the field just pinned) and one every period after, up to the
        end of its window (CONVENTION): 3 h every 10 min is 19."""
        return int(math.floor(self.window_s / self.every_s + 1e-9)) + 1

    def due(self, n: int) -> float:
        return self.started_at + n * self.every_s

    @property
    def grace(self) -> float:
        return min(self.every_s / 2.0, GRACE_MAX_S)

    def next_n(self) -> int | None:
        """The next slot with no row yet (0 the baseline), or None when every slot has one."""
        n = len(self.ledger)
        return n if n < self.slots else None

    def next_due(self) -> float | None:
        n = self.next_n()
        return self.due(n) if n is not None else None

    def counts(self) -> dict:
        out = {k: 0 for k in OUTCOMES}
        for s in self.ledger:
            out[s.outcome] = out.get(s.outcome, 0) + 1
        return out

    def wire(self) -> dict:
        return {"operation": self.operation, "everyS": self.every_s, "windowS": self.window_s, "convention": CONVENTION,
                "startedAt": self.started_at, "pinned": dict(self.pinned), "checkpointEvery": self.checkpoint_every,
                "status": self.status, "whyEnded": self.why_ended, "slots": self.slots, "nextDue": self.next_due(),
                "counts": self.counts(), "ledger": [s.row() for s in self.ledger], "origin": dict(self.origin), "id": self.id,
                **({"notesFrom": self.notes_from} if self.notes_from is not None else {}),
                "authority": copy.deepcopy(self.authority), "modelRuns": copy.deepcopy(self.model_runs),
                **({"acquisition": dict(self.acquisition)} if self.acquisition else {})}

    @classmethod
    def from_wire(cls, rid: str, d: dict) -> "Mission | None":
        """A saved mission read back, or None when its plan can't be (a hand-edited file: the mission is dropped,
        never its researcher). Rows a hand-edited file broke are dropped too; after a restart its status is
        "interrupted" until it is carried on."""
        if not isinstance(d, dict):
            return None
        try:
            every, window, start = float(d["everyS"]), float(d["windowS"]), float(d["startedAt"])
            checkpoint_every = int(d.get("checkpointEvery") or 3)
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        operation = str(d.get("operation") or "")
        minimum = ACQUISITION_MIN_EVERY_S if operation == "capture" else MIN_EVERY_S
        if not all(map(math.isfinite, (every, window, start))) or every < minimum or not 0 < window <= MISSION_MAX_S:
            return None
        try:
            acquisition = acquisition_parameters(operation, d.get("acquisition"), every)
        except ValueError:
            return None
        if d.get("convention", CONVENTION) != CONVENTION:
            return None  # counted another way than this code counts: never read as if it were this one's
        pinned = d.get("pinned")
        if not isinstance(pinned, dict) or not pinned:
            return None  # its field can't be read back, so no slot could be compared with it (#196 re-check SF)
        operation = str(d.get("operation") or "")
        ledger = []
        for r in d.get("ledger") if isinstance(d.get("ledger"), list) else []:
            if not isinstance(r, dict) or not isinstance(r.get("n"), int) or r.get("outcome") not in OUTCOMES:
                continue
            if isinstance(r["n"], bool) or not _finite_number(r.get("due")) or r["n"] != len(ledger):
                continue  # rows are numbered in order: one out of place is not this mission's
            if r.get("ran_at") is not None and not _finite_number(r["ran_at"]):
                continue
            # A row without its operation ran the mission's one operation (#196 re-check SF: never a TypeError
            # that drops the whole researcher).
            ledger.append(Slot(**{**{k: r.get(k) for k in Slot.__dataclass_fields__ if k in r},
                                  "operation": str(r.get("operation") or operation)}))
        m = cls(rid, operation, every, window, start, dict(pinned),
                max(1, checkpoint_every), str(d.get("status") or "interrupted"), ledger, str(d.get("whyEnded") or ""))
        m.acquisition = acquisition
        m.origin = dict(d["origin"]) if isinstance(d.get("origin"), dict) else {}
        if isinstance(d.get("id"), str) and d["id"]:
            m.id = d["id"]
        if isinstance(d.get("notesFrom"), int) and not isinstance(d["notesFrom"], bool) and d["notesFrom"] >= 0:
            m.notes_from = d["notesFrom"]
        m.authority = dict(d["authority"]) if isinstance(d.get("authority"), dict) else {}
        if isinstance(d.get("modelRuns"), list):
            for r in d["modelRuns"]:
                if (isinstance(r, dict) and r.get("missionId") == m.id and isinstance(r.get("runId"), str)
                        and isinstance(r.get("run"), int) and not isinstance(r["run"], bool) and r["run"] >= 0
                        and isinstance(r.get("origin"), dict) and r.get("missionPhase") in ("checkpoint", "finishing")
                        and (r.get("dispatchedAt") is None or _finite_number(r["dispatchedAt"]))
                        and (r.get("completedAt") is None or _finite_number(r["completedAt"]))):
                    m.model_runs.append(copy.deepcopy(r))
        if m.status not in STATUSES or m.status in ("watching", "checkpoint", "finishing"):
            m.status = "interrupted"
        return m


def acquisition_parameters(operation: str, value: dict | None, every_s: float) -> dict:
    if operation != "capture":
        if value:
            raise ValueError("acquisition parameters belong only to camera capture missions")
        return {}
    if not isinstance(value, dict) or set(value) != {"seconds"}:
        raise ValueError("camera capture missions require an explicit seconds duration")
    seconds = value["seconds"]
    if not _finite_number(seconds) or not 1.5 <= seconds <= 10 or seconds >= every_s:
        raise ValueError("each camera clip must last 1.5–10 seconds, shorter than its slot period")
    return {"seconds": float(seconds)}


def plan(rid: str, operation: str, every_s: float, window_s: float, pinned: dict, now: float,
         operations: tuple[str, ...], checkpoint_every: int = 3, *, acquisition: dict | None = None) -> Mission:
    """A mission's plan, checked: refused, with the reason, when it can't be run as asked."""
    if operation not in operations:
        raise ValueError(f"a mission runs one of: {', '.join(operations)}; not {operation!r}")
    if not all(map(math.isfinite, (float(every_s or 0), float(window_s or 0)))):
        raise ValueError("a mission's period and window are numbers of seconds")
    minimum = ACQUISITION_MIN_EVERY_S if operation == "capture" else MIN_EVERY_S
    if not every_s or every_s < minimum:
        raise ValueError(f"slots are at least {minimum:.0f} s apart")
    parameters = acquisition_parameters(operation, acquisition, every_s)
    if not window_s or window_s > MISSION_MAX_S:
        raise ValueError(f"a mission runs for at most {MISSION_MAX_S / 3600:.0f} hours for now")
    if window_s < every_s:
        raise ValueError("a mission's window holds at least one slot")
    if not pinned:
        raise ValueError("there is no live view to pin the field to")
    try:
        checkpoint = max(1, int(checkpoint_every))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("a mission's checkpoint interval is a finite number of slots") from None
    return Mission(rid, operation, float(every_s), float(window_s), float(now), dict(pinned), checkpoint,
                   acquisition=parameters)


def field_now(pinned: dict, now: dict, objective: Any = None) -> str:
    """Why the view now is not the pinned field, or "" when it is: the same run of the app, motion epoch (or a rejoin
    of it), picture size, eyepiece scale and objective, and the slide within SLIDE_SHARE of the eyepiece's radius of
    where it was pinned."""
    if not now:
        return "there is no live view now"
    if pinned.get("run") is not None and now.get("run") != pinned.get("run"):
        return "the app restarted since the field was pinned"
    if now.get("epoch") != pinned.get("epoch"):
        return "the slide's motion lost continuity since the field was pinned"
    if (now.get("width"), now.get("height")) != (pinned.get("width"), pinned.get("height")):
        return "the camera's picture size changed"
    if now.get("tracked") is False:
        return "the slide's motion was not registered on the view now, so where it is can't be told"
    a, b = float(pinned.get("discPx") or 0.0), float(now.get("discPx") or 0.0)  # the eyepiece's radius (view_facts)
    if a > 0 and b > 0 and abs(a - b) / max(a, b) > GEOMETRY_SHARE:  # contract 3's (apply.py): a refocus is 1-2.4%
        return "the eyepiece's scale changed (the lens refocused or zoomed)"
    if pinned.get("objective") is not None and objective is not None and str(objective) != str(pinned["objective"]):
        return f"the objective changed (pinned at {pinned['objective']}x, now {objective}x)"
    moved = math.hypot(float(now.get("dx") or 0.0) - float(pinned.get("dx") or 0.0),
                       float(now.get("dy") or 0.0) - float(pinned.get("dy") or 0.0))
    radius = a or b
    if radius and moved > SLIDE_SHARE * radius:
        return f"the slide moved {moved:.0f} px from the pinned field"
    return ""


class Missions:
    """The scheduler: one task per mission, its slots in order, each row written once (`save`) as it happens.

    `run_slot(mission, slot)` -> {"ok", "id", "count", "capture", "error", "busy", "off_field"}: the slot's operation
    through the researcher's hands ("busy": another research operation holds the app's one slot, asked again within
    the grace; "off_field": the view it ran on turned out not to be the pinned field, said so, its entry kept).
    `field(mission)` -> "" or why the view now is not the pinned field. `ends(mission)` -> "" or why the mission ends
    now (the owner's say; the research manager's says nothing ends one early: midnight does nothing, ADR 0017),
    checked every second: it is cancelled then, no row written. `checkpoint(mission)`: awaited every
    `checkpoint_every` done slots (P2c-2: the researcher reads them). `finish(mission)`: awaited once at the end.

    Due times are the wall clock's labels; waiting is on `mono` (monotonic), anchored to the start, so a step of the
    wall clock never bursts or stalls the slots. A cancelled acquisition retains its failed attempt row;
    legacy analysis slots retain their previous no-row cancellation behavior."""

    def __init__(self, run_slot: Callable[[Mission, Slot], Awaitable[dict]], field: Callable[[Mission], str],
                 save: Callable[[Mission, Slot | None], None],
                 checkpoint: Callable[[Mission], Awaitable[None]] | None = None,
                 finish: Callable[[Mission], Awaitable[None]] | None = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 log: Callable[[dict], None] = lambda record: None, ends: Callable[[Mission], str] | None = None,
                 mono: Callable[[], float] | None = None) -> None:
        self.run_slot, self.field, self.save = run_slot, field, save
        self.checkpoint, self.finish, self.ends = checkpoint, finish, ends
        self.clock, self.sleep, self.log = clock, sleep, log
        self.mono = mono or (clock if clock is not time.time else time.monotonic)
        self.active: dict[str, Mission] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self._anchor: dict[str, float] = {}  # rid -> the monotonic time of its start (wall started_at)

    def start(self, mission: Mission) -> asyncio.Task:
        running = self.active.get(mission.rid)
        if running is not None and running.status in ("watching", "checkpoint", "finishing"):
            raise ValueError(f"{mission.rid} already has a mission running")
        mission.status = "watching"
        self.active[mission.rid] = mission
        self._anchor[mission.rid] = self.mono() - (self.clock() - mission.started_at)
        task = asyncio.ensure_future(self._run(mission))
        self.tasks[mission.rid] = task
        return task

    def carry_on(self, mission: Mission) -> asyncio.Task:
        """An interrupted mission carried on (after a restart): the slots due while it was not running are rows
        "missed: the app was not running", never run late; it goes on from the next slot still to come. Only an
        interrupted one: a cancelled or ended mission stays as it is."""
        if mission.status != "interrupted":
            raise ValueError(f"only an interrupted mission is carried on; this one is {mission.status}")
        now = self.clock()
        while (n := mission.next_n()) is not None and mission.due(n) + mission.grace < now:
            self._write(mission, Slot(n, mission.due(n), mission.operation, "missed", "the app was not running"))
        return self.start(mission)

    def cancel(self, rid: str, why: str = "cancelled", mission: Mission | None = None) -> Mission | None:
        """Its future slots stop; what ran stays (the finding is written on it, P2c-2). `mission`: one not running
        here (interrupted), cancelled all the same."""
        mission = self.active.get(rid) or mission
        task = self.tasks.get(rid)
        if task is not None and not task.done():
            task.cancel()
        if mission is not None and mission.status not in ("done", "cancelled", "failed"):
            mission.status, mission.why_ended = "cancelled", why
            self.save(mission, None)
        return mission

    def _write(self, mission: Mission, slot: Slot) -> None:
        mission.ledger.append(slot)
        self.save(mission, slot)  # once, as it happens
        self.log({"kind": "mission-slot", "rid": mission.rid, **slot.row()})

    def _late(self, mission: Mission, n: int) -> float:
        """How late slot n is now (negative: still to come), by the monotonic clock from its start."""
        anchor = self._anchor.get(mission.rid, self.mono() - (self.clock() - mission.started_at))
        return self.mono() - (anchor + n * mission.every_s)

    def _ended(self, mission: Mission) -> bool:
        """It ends now (its owner said why): cancelled with why, saved, from its own task (which then returns, never
        cancelled)."""
        why = self.ends(mission) if self.ends is not None else ""
        if why and mission.status not in ("done", "cancelled"):
            mission.status, mission.why_ended = "cancelled", why
            self.save(mission, None)
        return bool(why)

    async def _wait_for(self, mission: Mission, n: int) -> bool:
        """Waits until slot n is due; False when the mission ended meanwhile (checked every second)."""
        while (left := -self._late(mission, n)) > 0:
            if self._ended(mission):
                return False
            await self.sleep(min(left, 1.0))
        return not self._ended(mission)

    async def _slot(self, mission: Mission, n: int) -> Slot:
        due = mission.due(n)
        slot = Slot(n, due, mission.operation)
        while True:
            if self._late(mission, n) > mission.grace:
                slot.outcome = "missed"
                slot.why = slot.why or f"it could not start within {mission.grace:.0f} s of its time"
                return slot
            now = self.clock()
            why = self.field(mission)
            if why:
                slot.outcome, slot.why, slot.ran_at = "not comparable", why, round(now, 3)
                return slot
            try:
                result = await self.run_slot(mission, slot)
            except asyncio.CancelledError:
                if mission.acquisition:
                    slot.outcome, slot.why, slot.ran_at = "failed", "acquisition interrupted by cancellation", round(now, 3)
                    self._write(mission, slot)
                raise
            except Exception as exc:
                # The operation was attempted, but supplied no capture or entry facts. Keep
                # that failed attempt before ending the mission; never invent a source frame.
                slot.outcome, slot.why, slot.ran_at = "failed", str(exc)[:240], round(now, 3)
                self._write(mission, slot)
                raise
            if result.get("busy"):
                slot.why = "another researcher's operation held the app's one slot past its grace"
                await self.sleep(BUSY_RETRY_S)
                continue
            slot.ran_at = round(now, 3)
            if result.get("ok"):
                slot.outcome, slot.why = "done", ""
                slot.entry, slot.count, slot.capture = result.get("id"), result.get("count"), result.get("capture")
                if result.get("off_field"):  # the view it ran on was not the field after all: kept, never compared
                    slot.outcome, slot.why = "not comparable", str(result["off_field"])[:240]
            else:
                slot.outcome, slot.why = "failed", str(result.get("error") or "it failed")[:240]
            return slot

    async def _run(self, mission: Mission) -> None:
        done_since = 0
        try:
            while (n := mission.next_n()) is not None:
                if not await self._wait_for(mission, n):
                    return  # it ended: cancelled, said in why_ended, no row for a slot that never ran
                slot = await self._slot(mission, n)
                self._write(mission, slot)
                done_since += slot.outcome == "done"
                if (not mission.acquisition and self.checkpoint is not None
                        and done_since >= mission.checkpoint_every and mission.next_n() is not None):
                    mission.status, done_since = "checkpoint", 0
                    self.save(mission, None)
                    await self.checkpoint(mission)  # its slots wait: never one during its own checkpoint
                    if mission.status == "checkpoint":
                        mission.status = "watching"
                    else:
                        return
            mission.status = "finishing"
            self.save(mission, None)
            if self.finish is not None:
                await self.finish(mission)
            if mission.status != "finishing":
                return
            mission.status, mission.why_ended = "done", mission.why_ended or "every slot has a row"
            self.save(mission, None)
        except asyncio.CancelledError:
            # Retiring stale scheduler work cannot turn an earlier timeout failure into user cancellation.
            if mission.status not in ("done", "cancelled", "failed"):
                mission.status, mission.why_ended = "cancelled", mission.why_ended or "cancelled"
                self.save(mission, None)
            raise
        except Exception as exc:
            # A checkpoint or final finding that did not run is not completed work. Keep the
            # ledger and the failure visible, and let the owner release the unused reservation.
            if mission.status in ("done", "cancelled", "failed"):
                return
            phase = mission.status
            mission.status, mission.why_ended = "failed", f"{phase}: {exc}"[:240]
            self.save(mission, None)
            self.log({"kind": "mission-failed", "rid": mission.rid, "phase": phase, "error": mission.why_ended})
        finally:
            if self.active.get(mission.rid) is mission:  # a new mission started since is never taken off
                self.active.pop(mission.rid, None)
