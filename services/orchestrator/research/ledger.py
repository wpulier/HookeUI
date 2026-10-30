"""The ledger (CONTEXT.md): what researchers spent, per calendar day, and the daily cap.

A session can span days and a day can hold several sessions (ADR 0017), so spend is charged to the calendar day a
run happens on (ADR 0004 as amended): each run keeps the day it started on (`usageRuns[...]["day"]`), whichever date
folder its researcher is filed in. A day's spend is every session's spend that day:
- the researchers in memory (estimated cut-off calls included), each by the day of each of its runs;
- the researchers only on disk: the ones filed in that day's folder, and the ones filed in another day's folder that
  ran that day, listed in the day's `runs.jsonl` as each such run starts, before it can spend anything (`note_run`);
- what a day spent before it was started afresh, before sessions existed (`spent-carried.json`).
Spend saved before runs kept their day, and any spend of no run, counts on the researcher's folder's date, as before.
Days are kept apart, so a run started today never counts against yesterday's cap, nor yesterday's against today's.

The cap is unlimited unless the user sets one (Settings.research_cap). When there is one, a run reserves its effort's
maximum when it starts and gives back what it did not spend when it ends, so the day never goes over the cap
(ADR 0004).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Iterable

CARRIED = "spent-carried.json"  # what a day spent before it was started afresh (before sessions), still counted
RUNS = "runs.jsonl"  # runs that day of researchers filed in another day's folder: {"rid", "day" (its folder), "at"}




def never_billed(job: dict) -> bool:
    """A presentation the provider refused before any work: HTTP 429, or a refusal for want of credit (engines.py's rule:
    `billed = why != 'HTTP 429'`). Its estimate is no spend. A job saved before that rule kept its reservation as its
    estimate (live 2026-10-25: two such refusals counted $0.80 that Spending never saw), so its saved words are read too."""
    if job.get('receipt'):
        return False  # its receipt says what it cost
    if job.get('providerStatus') == 429:
        return True
    words = f"{job.get('errorDetail') or ''} {job.get('error') or ''}"
    from research_worker.api_usage import CREDIT_CODES  # the one mapping's codes, read in a retained job's raw words

    return words.lstrip().startswith('Error code: 429') or any(code in words for code in CREDIT_CODES) \
        or ' has no credits left' in words


def presentation_estimate(job: dict) -> float:
    """What a presentation job may have cost and has no receipt for yet (nothing when it was never billed)."""
    return 0.0 if never_billed(job) else float(job.get('estimatedCost') or 0)


def presentation_spend(jobs) -> float:
    return sum(float(job.get('cost') or 0) + presentation_estimate(job) for job in jobs)


def by_day(folder: str, spent: float, estimated: float, runs: dict | None, jobs: list | None,
           *, estimates_only: bool = False) -> dict[str, float]:
    """What a researcher filed in `folder` spent on each calendar day: each run on the day it started (an older run
    without one on the folder's date), the rest of its spend (from before runs were kept) on the folder's date, and
    each presentation job on its own day (else the folder's). `estimates_only`: only what is estimated (calls with no
    usage, presentations with no receipt yet), the part the studio says as "up to … more, receipts pending"."""
    days: dict[str, float] = {}
    counted = 0.0
    for run in (runs or {}).values():
        if not isinstance(run, dict):
            continue
        amount = float(run.get("estimated") or 0) + (0.0 if estimates_only else float(run.get("spent") or 0))
        day = run["day"] if isinstance(run.get("day"), str) and run["day"] else folder
        days[day] = days.get(day, 0.0) + amount
        counted += amount
    whole = float(estimated or 0) + (0.0 if estimates_only else float(spent or 0))
    days[folder] = days.get(folder, 0.0) + max(0.0, whole - counted)
    for job in jobs or []:
        if isinstance(job, dict):
            day = job["day"] if isinstance(job.get("day"), str) and job["day"] else folder
            days[day] = days.get(day, 0.0) + (presentation_estimate(job) if estimates_only else presentation_spend([job]))
    return days


def spend_of(session, *, estimates_only: bool = False) -> dict[str, float]:
    """A researcher in memory (manager.Session), by day."""
    return by_day(session.day, session.spent, session.spent_estimated, session.usage_runs,
                  getattr(session, "presentation_jobs", None), estimates_only=estimates_only)


def _month_ledger():
    """The app's usage ledger, holding the optional monthly cap on cloud models (research_worker.api_usage), or None."""
    from research_worker import api_usage

    return api_usage._active


class DayLedger:
    def __init__(self, root: Path, today: Callable[[], str], sessions: Callable[[], Iterable]) -> None:
        self.root = root
        self.today = today
        self.sessions = sessions  # the researchers in memory (manager.sessions.values), of every session
        # day -> (the researchers in memory when it was read, that day's spend only on disk, the estimated part of it):
        # read again only when the researchers in memory change (nothing else writes a researcher not in memory).
        self._on_disk: dict[str, tuple[frozenset, float, float]] = {}
        # a saved researcher's spend by day, and the estimated part of it by day
        self._files: dict[Path, tuple[tuple[int, int], dict[str, float], dict[str, float]]] = {}
        self._held: dict[str, tuple[float, float]] = {}  # rid -> (reserved usd, its spend when reserved)
        self._held_day: dict[str, str] = {}  # the day each reservation is charged to (the day its run started)
        self._month_hold: dict[str, str] = {}  # rid -> its run's own row against the monthly cap (api_usage), released at its end

    def spent(self, day: str | None = None) -> float:
        day = day or self.today()
        here, cached = self._day(day)
        return cached[1] + sum(spend_of(s).get(day, 0.0) for s in here)

    def estimated(self, day: str | None = None) -> float:
        """The part of `spent(day)` that is estimated, not billed as such yet: calls that returned no usage and
        presentations with no receipt (ones never billed count nothing), of every researcher, as spent() counts them."""
        day = day or self.today()
        here, cached = self._day(day)
        return cached[2] + sum(spend_of(s, estimates_only=True).get(day, 0.0) for s in here)

    def _day(self, day: str) -> tuple[list, tuple[frozenset, float, float]]:
        here = list(self.sessions())
        loaded = frozenset(s.rid for s in here)
        cached = self._on_disk.get(day)
        if cached is None or cached[0] != loaded:
            cached = self._on_disk[day] = (loaded, *self._read(day, loaded))
        return here, cached

    def committed(self) -> float:
        """Today's spend plus what running researchers may still spend (reservations made today)."""
        today = self.today()
        running = {s.rid: s for s in self.sessions()}
        held = 0.0
        for rid, (usd, before) in self._held.items():
            if self._held_day.get(rid, today) != today:
                continue  # a run started on an earlier day is charged to that day
            s = running.get(rid)
            if s is None:
                held += usd  # still starting or future mission runs: its whole reservation counts
            else:
                held += max(0.0, usd - (s.spent + s.spent_estimated - before))
        return self.spent(today) + held

    def check(self, cap: float | None, usd: float) -> None:
        """Raise ResearchRefused when a run of up to `usd` would take today over `cap`."""
        from .policy import ResearchRefused

        monthly = _month_ledger()
        if monthly is not None and not monthly.fits(usd):  # the optional monthly cap on cloud models (api_usage, C7)
            month = monthly.month()
            raise ResearchRefused(f"the monthly cap you set for cloud models (${month['cap']:.2f}) has about "
                                  f"${max(0.0, month['cap'] - month['estimatedUsd']):.2f} left this month, and this run "
                                  f"could cost up to ${usd:.2f}; raise or remove it in the spending panel", next=None)  # no next: the message identifies the applicable limit and how to change it
        if cap is None:
            return
        left = cap - self.committed()
        if usd > left + 1e-9:
            raise ResearchRefused(f"today's research budget (${cap:.2f}) has ${max(0.0, left):.2f} left, and this "
                                  f"could cost up to ${usd:.2f}; the cap can be raised or removed in the settings", next=None)  # no next: the message says what to do

    def hold(self, session, usd: float) -> None:
        monthly = _month_ledger()
        if monthly is not None:  # its most, held against the month too until it ends (settled by release)
            from research_worker.api_usage import CapReached

            from .policy import ResearchRefused

            import uuid

            # Each run its own row, written in the month it starts (Codex's diagnosis of 89e834b5, point 1: a key reused
            # by a researcher's later run kept its first run's month, so a new month never counted it).
            key = f"research-hold:{session.rid}:{time.strftime('%Y-%m')}:{uuid.uuid4().hex[:12]}"
            try:
                monthly.admit("research run", "Research", "Research", usd, key=key)
            except CapReached as exc:
                raise ResearchRefused(str(exc), next=None) from exc  # no next: the message identifies the applicable limit and how to change it
            self._month_hold[session.rid] = key
        self._held[session.rid] = (usd, session.spent + session.spent_estimated)
        self._held_day[session.rid] = self.today()

    def reserve(self, key: str, usd: float) -> None:
        """A reservation that is not one run's: a mission's model runs still to come (missions.py), counted whole
        until it is changed or released. Its key is never a researcher's id, so it is never netted against a spend."""
        self._held[key] = (max(0.0, float(usd)), 0.0)
        self._held_day[key] = self.today()

    def release(self, rid: str) -> None:
        self._held.pop(rid, None)
        self._held_day.pop(rid, None)
        key = self._month_hold.pop(rid, None)
        monthly = _month_ledger()
        if monthly is not None and key is not None:  # its own charges, recorded as they came, count instead
            monthly.settle(key, 0.0, "Research run's hold: its charges are counted as they came")

    def note_run(self, rid: str, folder: str, day: str) -> None:
        """A run starting on `day` of a researcher filed in another day's `folder` (a session's researcher continued on a
        later day): listed in that day's runs.jsonl before it can spend, so the day's spend finds it after a restart.
        Raises OSError when it can't be written: then the run does not start."""
        if day == folder:
            return
        directory = self.root / day
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / RUNS).open("a") as f:
            f.write(json.dumps({"rid": rid, "day": folder, "at": round(time.time(), 3)}) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._on_disk.pop(day, None)

    def elsewhere(self, day: str) -> set[tuple[str, str]]:
        """(folder, rid) of every researcher filed in another day's folder that ran on `day`."""
        found = set()
        try:
            lines = (self.root / day / RUNS).read_text().splitlines()
        except OSError:
            return found
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a line torn by a crash: its run was refused (note_run raised) or wrote again
            if isinstance(row, dict) and isinstance(row.get("rid"), str) and isinstance(row.get("day"), str):
                found.add((row["day"], row["rid"]))
        return found

    def _read(self, day: str, loaded: frozenset) -> tuple[float, float]:
        """That day's spend of the researchers only on disk, and the estimated part of it."""
        total = estimated = 0.0
        try:
            total += float(json.loads((self.root / day / CARRIED).read_text()).get("spent") or 0)
        except (OSError, ValueError, AttributeError):
            pass
        paths = [p for p in (self.root / day).glob("*/session.json")]
        paths += [self.root / folder / rid / "session.json" for folder, rid in sorted(self.elsewhere(day))
                  if folder != day and "/" not in folder and "/" not in rid]
        for path in paths:
            if path.parent.name in loaded:
                continue
            days, estimates = self._saved_parts(path)
            total += days.get(day, 0.0)
            estimated += estimates.get(day, 0.0)
        return total, estimated

    def _saved(self, path: Path) -> dict[str, float]:
        """A saved researcher's spend by day, read again only when its file changed."""
        return self._saved_parts(path)[0]

    def _saved_parts(self, path: Path) -> tuple[dict[str, float], dict[str, float]]:
        """A saved researcher's spend by day and the estimated part of it, read again only when its file changed."""
        try:
            stat = path.stat()
        except OSError:
            return {}, {}
        stamp = (stat.st_mtime_ns, stat.st_size)
        known = self._files.get(path)
        if known is not None and known[0] == stamp:
            return known[1], known[2]
        try:
            saved = json.loads(path.read_text())
            parts = (path.parent.parent.name, float(saved.get("spent") or 0), float(saved.get("spentEstimated") or 0),
                     saved.get("usageRuns") if isinstance(saved.get("usageRuns"), dict) else None,
                     saved.get("presentationJobs") if isinstance(saved.get("presentationJobs"), list) else None)
            days, estimates = by_day(*parts), by_day(*parts, estimates_only=True)
        except (OSError, ValueError, TypeError, AttributeError):
            days, estimates = {}, {}
        self._files[path] = (stamp, days, estimates)
        return days, estimates
