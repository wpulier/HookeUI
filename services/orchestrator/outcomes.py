"""Outcomes (session knowledge K3, contract 4 of docs/design/2026-10-07-session-knowledge.md; ADR 0013 "Outcomes
are facts; the app recommends only what won"): what happened when an implementation ran, for the brain to look
up when it chooses, or for the user who asks "which model did better on this smear today?".

- **An outcome** is the kept record of an Execution (the interaction log's `execution` lines, written by the
  runner, scaffolding Amendment 1), with only the unambiguous signals attached to it by execution id:
  - the user's corrections of its members (evidence revisions: deletions, merges, undos; per 100 members);
  - blind votes of a trial that compared it (an outline trial's arms), won or lost only for a letter winner, ties
    and "none good" said apart, never pooled across policies;
  - finding nothing, failing (the model's own error), and its time, end to end (fresh runs only: a reuse is never
    a run).
  What the user does that could mean several things (removing an outline, a cleanup) is never counted.
- **Only runs where the model ran count** (K3 review B2). A run cancelled before it began, replaced in the queue by a
  newer one, turned away by a full queue, expired there, refused in words, or that could not run here is kept out
  of the runs and said apart ("not counted: 3 replaced by a newer one before running"); "default" (nothing was
  chosen because nothing could run) is never a line.
- **Aggregated** by subject (`labels.subject_key`), operation, implementation, policy and whether it was asked for
  or a background run (K2's re-measuring of followed things is kept apart): today, and earlier days with their
  dates. Never generalised across subjects: asked with no specimen named, every line names its own specimen.
  Below `MIN_RUNS` a figure is "too few runs to say", never a rate.
- **Pulled, not pushed:** the `outcomes` query tool. Pushing lines into the brain's prompt is option
  OUTCOME_LINES, off by default; it never reaches the fast paths. Code never switches or hides a route by them.
- **Read cheaply** (K3 review SF1): the log is read once and afterwards only what was added to it (by file and
  offset), keeping each execution's few facts, never its line; an evidence manifest is summarised once per change
  (its modification time), and only the days asked about are read. Today's corrections come from memory first.
"""
from __future__ import annotations

import json
import re
import statistics
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

MIN_RUNS = 3  # fewer runs than this: "too few runs to say", never a rate
READ_MAX_BYTES = 64 * 1024 * 1024  # the interaction log read for outcomes at first, newest first
LINES_MAX = 12  # lines one answer gives, newest day first (the rest said by how many and from when; K3 review SF4)
COMPARED = {"outline": "outline"}  # a trial's kind -> the operation its arms ran (an ask trial compares vision models)
# Why a run never ran, in words that take a number ("2 cancelled before running").
NOT_RUN_WORDS = {"cancelled": "cancelled before running", "superseded": "replaced by a newer one before running",
                 "busy": "turned away while the queue was full", "expired": "expired in the queue before running",
                 "hung": "never sent while the worker was not answering", "unavailable": "couldn't run here",
                 "unread": "whose input could not be read", "refused": "refused their input before running",
                 "stopped": "stopped by you while they ran"}
_UNREAD = ("can't be read now", "could not be read back", "were not kept")  # an input not read, in older lines' words
PUSH_FRESH_S = 2.0  # OUTCOME_LINES: lines made this recently are pushed as they are (made in a thread, never on the loop)
_RAW = re.compile(r"^[A-Z][A-Za-z]*(?:Error|Exception|Fault|Exit|Interrupt|Timeout)\b: ")  # operations.RAW_ERROR


def attempt_state(a: dict) -> str:
    """"ok", "failed" (the model ran and raised), or why it never ran (operations.NOT_RUN). Lines written before the
    runner said why (`notRun`) are read from their words."""
    if a.get("ok"):
        return "ok"
    if a.get("notRun"):
        return str(a["notRun"])
    error = str(a.get("error") or "")
    if a.get("why") == "unavailable":
        return "unavailable"
    if not error:
        return "cancelled"  # no error and not ok: the attempt was given up before it ended (CancelledError)
    if error.startswith("Superseded:"):
        return "superseded"
    if error.startswith("Busy:"):
        return "busy"
    if error.startswith(("TimeoutError:", "ExpiredError:")) and ("waited past" in error or "never run" in error):
        return "expired"
    if error.startswith(("RuntimeError:", "WorkerHungError:")) and "worker is not responding" in error:
        return "hung"  # the worker was already marked hung: the call was never sent
    if not _RAW.match(error):
        # words, not an exception: an input that could not be read, or a refusal (a ToolFailed); neither ran
        return "unread" if any(w in error for w in _UNREAD) else "refused"
    return "failed"


def run_state(record: dict) -> str:
    """One execution: "ok", "empty" (it ran and found nothing), "failed" (the model's own error), or why it never ran."""
    attempts = record.get("attempts") or []
    states = [attempt_state(a) for a in attempts]
    ran = [(a, s) for a, s in zip(attempts, states) if s in ("ok", "failed")]
    if not ran:
        if record.get("executed") is not None and not attempts:
            return "ok"
        return next((s for s in reversed(states)), "unavailable")
    oks = [a for a, s in ran if s == "ok"]
    if record.get("executed") is None and any(s == "failed" for _a, s in ran):
        return "failed"
    if oks and all(a.get("empty") for a in oks):
        return "empty"
    return "ok"


@dataclass(slots=True)
class Run:
    """The few facts of one execution line the tallies need (never the line itself)."""
    id: str
    operation: str
    implementation: str
    policy: str
    background: bool
    subject: str
    at: float
    ms: float | None
    state: str
    version: str = ""  # fine-tuned weights: the version that ran ("v2"); each version is its own line (K4)

    @property
    def day(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.at or 0))


def run_of(record: dict) -> Run | None:
    """A Run from an `execution` line (a trial's only once its vote revealed it), or None for what is not one."""
    kind = record.get("kind")
    if not record.get("id") or record.get("reused"):
        return None
    if kind == "execution" and record.get("trial"):
        return None  # blind until its vote (then an `execution-revealed` line says it in full)
    if kind not in ("execution", "execution-revealed"):
        return None
    if record.get("origin") in ("replay", "check"):
        # The replay bench running a past view again (K4), or the capability check running each model once on its
        # own frame (model_checks.py): never an outcome of the session.
        return None
    impl = record.get("executed") or record.get("requested") or ""
    state = run_state(record)
    ms = float(record["ms"]) if record.get("executed") is not None and record.get("ms") else None
    return Run(str(record["id"]), str(record.get("operation") or ""), str(impl), str(record.get("policy") or "route"),
               record.get("origin") == "background", str(record.get("subject") or ""), float(record.get("startedAt") or 0.0),
               ms, state, str(record.get("version") or ""))


class _Log:
    """One interaction log, read once and then only what was added (by its inode and the offset read to); an
    execution's latest line wins. `cut` is the first day it reaches when its start was not read (READ_MAX_BYTES)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.inode: int | None = None
        self.end = 0
        self.cut: str | None = None
        self.runs: dict[str, Run] = {}

    def read(self) -> None:
        try:
            st = self.path.stat()
        except OSError:
            self.inode, self.end, self.runs, self.cut = None, 0, {}, None
            return
        skip_first = False
        if self.inode != st.st_ino or st.st_size < self.end:  # a new or replaced file: from its newest READ_MAX_BYTES
            self.inode, self.runs, self.cut = st.st_ino, {}, None
            self.end = max(0, st.st_size - READ_MAX_BYTES)
            skip_first = self.end > 0
        if st.st_size == self.end:
            return
        with self.path.open("rb") as f:
            f.seek(self.end)
            pos = self.end
            if skip_first:
                pos += len(f.readline())  # the first line may be cut
            first = skip_first
            for raw in f:
                if not raw.endswith(b"\n"):
                    break  # still being written: read next time
                pos += len(raw)
                if b'"execution' not in raw:
                    continue
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                found = run_of(record)
                if found is not None:
                    self.runs[found.id] = found
                    if first:
                        self.cut, first = found.day, False
            self.end = pos


@dataclass
class Tally:
    """One (subject, operation, implementation, policy, asked or background) on one day."""
    subject: str
    operation: str
    implementation: str
    policy: str
    background: bool
    day: str
    version: str = ""  # fine-tuned weights' version: each version is its own line (K4 review SF4)
    runs: int = 0
    empty: int = 0
    failed: int = 0
    ms: list = field(default_factory=list)
    members: int = 0  # members its populations had (revision 0), for corrections per 100
    deleted: int = 0
    merged: int = 0
    undone: int = 0
    won: int = 0
    lost: int = 0
    ties: int = 0
    none_good: int = 0
    not_run: dict = field(default_factory=dict)  # why -> how many (never counted as runs)
    executions: list = field(default_factory=list)
    last_at: float = 0.0  # the newest run's (or attempt's) start

    def label(self, subject: bool = False) -> str:
        from .operations import label_of

        name = "No model could run here" if self.implementation == "default" else label_of(self.implementation)
        return (f"{name}{f' {self.version}' if self.version else ''} ({self.policy}"
                + (", background re-measuring" if self.background else "")
                + ")" + ((f" on {self.subject}" if self.subject else " with no specimen named") if subject else ""))

    def words(self, today: str, label: str | None = None, subject: bool = False) -> str:
        """One past-tense line, with n; below MIN_RUNS "too few runs to say". `subject`: the line names its specimen
        (asked with none named: every specimen's lines are told apart, never as this one's)."""
        label = label or self.label(subject)
        when = "today" if self.day == today else f"on {self.day}"
        if self.implementation == "default":  # nothing was chosen because nothing could run: said apart, never a model's
            n = sum(self.not_run.values())
            return f"{label}: {n} time{'s' if n != 1 else ''} {when}, not counted"
        if self.runs == 0:
            text = f"{label}: no run {when}"
        elif self.runs < MIN_RUNS:
            text = f"{label}: {self.runs} run{'s' if self.runs != 1 else ''} {when} (too few runs to say)"
        else:
            parts = [f"{self.runs} runs {when}"]
            if self.empty:
                parts.append(f"found nothing {self.empty} time{'s' if self.empty != 1 else ''}")
            if self.failed:
                parts.append(f"failed {self.failed} time{'s' if self.failed != 1 else ''}")
            if self.members:
                fixes = self.deleted + self.merged
                parts.append(f"{100 * fixes / self.members:.1f} per 100 cells corrected by you"
                             + (f" ({self.deleted} removed, {self.merged} merged)" if fixes else ""))
            if self.ms:
                parts.append(f"{statistics.median(self.ms) / 1000:.1f} s median, waiting included")
            text = f"{label}: " + ", ".join(parts)
        return text + self._votes() + self._not_run()

    def _votes(self) -> str:
        if not (self.won or self.lost or self.ties or self.none_good):
            return ""
        parts = [f"won {self.won} of {self.won + self.lost}"] if self.won or self.lost else []
        if self.ties:
            parts.append(f"{self.ties} tie{'s' if self.ties != 1 else ''}")
        if self.none_good:
            parts.append(f"{self.none_good} with none good")
        return f"; blind votes ({self.policy}): " + ", ".join(parts)

    def _not_run(self) -> str:
        if not self.not_run:
            return ""
        return "; not counted: " + ", ".join(f"{n} {NOT_RUN_WORDS.get(why, why)}" for why, n in self.not_run.items())


class Outcomes:
    """Outcomes from the session's own records, read when asked (the log's and manifests' few facts are kept between
    asks so only what changed is read again; nothing is written)."""

    def __init__(self, logs: list[Path], evidence: Path | None, votes: Path | None, hub=None, more=None) -> None:
        self.more = more  # records not in a file (a stand-in world's log): a callable giving them
        self.logs = [Path(p) for p in logs]
        self.evidence = Path(evidence) if evidence is not None else None
        self.votes = Path(votes) if votes is not None else None
        self.hub = hub
        self._lock = threading.Lock()
        self._readers = {p: _Log(p) for p in self.logs}
        self._manifests: dict[Path, tuple[tuple[int, int], dict | None]] = {}
        self._votes: tuple[tuple[int, int], dict] | None = None
        self._pushed: dict[tuple, tuple[float, list[str]]] = {}  # OUTCOME_LINES: (subject, day) -> (made at, lines)
        self._pushing: set[tuple] = set()

    # --- what ran --------------------------------------------------------------------------------
    def runs(self) -> dict[str, Run]:
        """Every execution the logs keep (an arena trial's only once its vote revealed it), by id, the latest line."""
        out: dict[str, Run] = {}
        with self._lock:
            for path in reversed(self.logs):  # oldest file first: a newer line wins
                reader = self._readers[path]
                reader.read()
                out.update(reader.runs)
        for record in (list(self.more()) if self.more is not None else []):
            found = run_of(record)
            if found is not None:
                out[found.id] = found
        return out

    def reaches(self) -> str | None:
        """The first day the logs read reach back to, when their start was not read (READ_MAX_BYTES); else None."""
        cuts = [r.cut for r in self._readers.values() if r.cut]
        return min(cuts) if cuts else None

    # --- the user's corrections ---------------------------------------------------------------------
    def corrections(self, days=None) -> dict[str, dict]:
        """Per execution: its members (revision 0) and what the user's revisions did to them: today's populations as
        they are in memory first (saved or not, the newest), then the evidence manifests of `days` (all when None)."""
        out: dict[str, dict] = {}
        rows: list[tuple] = []
        wanted = set(days) if days is not None else None
        if self.hub is not None:
            from .evidence import evidence_of

            for p in list(evidence_of(self.hub).items.values()):  # copied: corrections run on the loop meanwhile
                if wanted is not None and getattr(p, "day", None) and p.day not in wanted:
                    continue
                execution = (p.made_by or {}).get("execution")
                if execution:
                    rows.append((execution, p.id, _summary([m.status for m in list(p.members.values())], list(p.revisions))))
        if self.evidence is not None and self.evidence.is_dir():
            folders = ([self.evidence / d for d in sorted(wanted)] if wanted is not None
                       else [f for f in self.evidence.iterdir() if f.is_dir()])
            with self._lock:
                for folder in folders:
                    if not folder.is_dir():
                        continue
                    for path in folder.glob("*/manifest.json"):
                        found = self._manifest(path)
                        if found is not None:
                            rows.append((found["execution"], found["id"], found["row"]))
        seen = set()
        for execution, pid, row in rows:  # memory first: a revision still being saved is not lost behind its disk copy
            if (execution, pid) in seen:
                continue
            seen.add((execution, pid))
            total = out.setdefault(execution, {"members": 0, "deleted": 0, "merged": 0, "undone": 0})
            for k in total:
                total[k] += row[k]
        return out

    def _manifest(self, path: Path) -> dict | None:
        try:
            st = path.stat()
        except OSError:
            return None
        stamp = (st.st_mtime_ns, st.st_size)
        cached = self._manifests.get(path)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        try:
            m = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        execution = m.get("execution") or (m.get("madeBy") or {}).get("execution")
        found = ({"execution": execution, "id": m.get("id"),
                  "row": _summary([x.get("status") for x in (m.get("members") or [])], m.get("revisions") or [])}
                 if execution else None)
        self._manifests[path] = (stamp, found)
        return found

    # --- votes -------------------------------------------------------------------------------------
    def votes_by_execution(self) -> dict[str, dict[str, dict]]:
        """Per execution a trial compared, per policy (never pooled): won, lost (only for a letter winner), ties and
        "none good", with the operation the trial compared. Each vote counts once per execution. The votes file is read
        again only when it changed (its modification time and size)."""
        if self.votes is None:
            return {}
        try:
            st = self.votes.stat()
        except OSError:
            return {}
        stamp = (st.st_mtime_ns, st.st_size)
        if self._votes is not None and self._votes[0] == stamp:
            return self._votes[1]
        out = _votes_of(self.votes.read_text())
        self._votes = (stamp, out)
        return out

    # --- the tallies -----------------------------------------------------------------------------------
    def tallies(self, operation: str | None = None, subject: str | None = None, days: str = "all",
                today: str | None = None) -> list[Tally]:
        """The tallies for an operation (or every one), today and earlier days (days="all") or today only, newest day
        first. `subject` keeps only that specimen's runs (never generalised across subjects); None keeps every specimen,
        each in its own tallies (their lines name it); "" keeps the runs made with no specimen named."""
        today = today or time.strftime("%Y-%m-%d")
        rows: dict[tuple, Tally] = {}
        picked: list[Run] = []
        for run in self.runs().values():
            if operation and run.operation != operation:
                continue
            if subject is not None and run.subject != subject:
                continue  # never generalised across subjects
            if days == "today" and run.day != today:
                continue
            if run.implementation in ("", "?"):
                continue
            if run.implementation == "default" and run.state not in NOT_RUN_WORDS:
                continue  # "default" is only ever a run that could not run: its own line, said apart
            picked.append(run)
        corrections = self.corrections({r.day for r in picked}) if picked else {}
        votes = self.votes_by_execution() if picked else {}
        for run in picked:
            key = (run.subject, run.operation, run.implementation, run.policy, run.background, run.day, run.version)
            t = rows.setdefault(key, Tally(*key))
            t.last_at = max(t.last_at, run.at)
            if run.state not in ("ok", "empty", "failed"):
                t.not_run[run.state] = t.not_run.get(run.state, 0) + 1
                continue  # it never ran: not a run, said apart
            t.runs += 1
            t.executions.append(run.id)
            if run.state == "failed":
                t.failed += 1
            elif run.state == "empty":
                t.empty += 1
            if run.ms is not None:
                t.ms.append(run.ms)
            c = corrections.get(run.id)
            if c:
                t.members += c["members"]
                t.deleted += c["deleted"]
                t.merged += c["merged"]
                t.undone += c["undone"]
            v = votes.get(run.id, {}).get(t.policy)  # a vote counts only under its own policy
            if v and v["operation"] == t.operation:
                t.won += v["won"]
                t.lost += v["lost"]
                t.ties += v["ties"]
                t.none_good += v["none"]
        return sorted(rows.values(), key=lambda t: (t.day, t.runs, t.last_at), reverse=True)

    def words(self, operation: str | None, subject: str | None, today: str, days: str = "today") -> list[str]:
        """Every line (newest day first). `subject` None: every specimen's, each line naming its own."""
        return [t.words(today, subject=subject is None) for t in self.tallies(operation, subject, days, today)]

    def answer(self, operation: str | None, subject: str | None, today: str, days: str = "today",
               limit: int = LINES_MAX) -> tuple[list[str], str]:
        """At most `limit` lines, newest day first, and a note of what was left out and how far back the log reaches."""
        tallies = self.tallies(operation, subject, days, today)
        lines = [t.words(today, subject=subject is None) for t in tallies[:limit]]
        notes = []
        rest = tallies[limit:]
        if rest:
            earlier = sorted({t.day for t in rest})
            span = earlier[0] if len(earlier) == 1 else f"{earlier[0]} to {earlier[-1]}"
            notes.append(f"and {len(rest)} more line{'s' if len(rest) != 1 else ''} ({span}) not given here: "
                         "ask about one operation, or today")
        reach = self.reaches() if days != "today" else None
        if reach:
            notes.append(f"the log read reaches back only to {reach}")
        return lines, "; ".join(notes)

    # --- what option OUTCOME_LINES pushes: made in a thread, never on the event loop (K3 re-check NOTE 2) ----------
    def make_pushed(self, subject: str | None, today: str) -> list[str]:
        """Today's lines for the brain's prompt (each naming its specimen when none is named), read now: call it in a
        thread (the brain's turn does, before it captures what the brain is told)."""
        lines = self.words(None, subject, today)
        self._pushed[(subject, today)] = (time.monotonic(), lines)
        return lines

    def pushed(self, subject: str | None, today: str) -> list[str]:
        """The lines last made for (subject, today), read on no caller's thread: when there are none, or they are older
        than PUSH_FRESH_S, they are made again in a thread for the next prompt ([] until there are some)."""
        key = (subject, today)
        found = self._pushed.get(key)
        if (found is None or time.monotonic() - found[0] > PUSH_FRESH_S) and key not in self._pushing:
            self._pushing.add(key)

            def make() -> None:
                try:
                    self.make_pushed(subject, today)
                except Exception:
                    pass
                finally:
                    self._pushing.discard(key)

            threading.Thread(target=make, name="outcome-lines", daemon=True).start()
        return list(found[1]) if found is not None else []


def _votes_of(text: str) -> dict[str, dict[str, dict]]:
    """The votes file's text, by execution and policy (Outcomes.votes_by_execution)."""
    out: dict[str, dict[str, dict]] = {}
    for line in text.splitlines():
        try:
            vote = json.loads(line)
        except ValueError:
            continue
        if vote.get("by") not in (None, "studio"):
            continue  # a harness's vote is not the user's
        compared = COMPARED.get(vote.get("kind") or "outline")
        if compared is None:
            continue  # an ask trial judged the vision models' answers, not the outlines they pointed with
        winner = vote.get("winner")
        for letter, ids in (vote.get("executions") or {}).items():
            for execution in dict.fromkeys(ids or []):
                row = out.setdefault(execution, {}).setdefault(
                    vote.get("policy") or "route-legacy", {"won": 0, "lost": 0, "ties": 0, "none": 0, "operation": compared})
                if winner == "tie":
                    row["ties"] += 1
                elif winner == "none":
                    row["none"] += 1
                elif winner == letter:
                    row["won"] += 1
                elif winner:
                    row["lost"] += 1
    return out


def _summary(statuses: list, revisions: list) -> dict:
    """What the user's revisions did to one population: its members at revision 0, removals, merges and undos."""
    added = {a for r in revisions for a in (r.get("added") or [])}
    row = {"members": len(statuses) - len(added), "deleted": 0, "merged": 0, "undone": 0}
    undone = {r.get("undoes") for r in revisions if r.get("undoes") is not None}
    for r in revisions:
        if r.get("r") in undone:
            row["undone"] += 1
            continue
        if r.get("undoes") is not None:
            continue
        if r.get("added"):
            row["merged"] += len(r.get("was") or {})
        else:
            row["deleted"] += len(r.get("was") or {})
    return row


def outcomes_of(hub) -> Outcomes:
    found = getattr(hub, "outcomes", None)
    if isinstance(found, Outcomes):
        return found
    from .paths import Paths

    try:
        paths = Paths.from_env()
        made = Outcomes([paths.interaction_log], paths.evidence, paths.votes_log, hub=hub)
    except Exception:
        made = Outcomes([], None, None, hub=hub)
    try:
        hub.outcomes = made
    except AttributeError:
        pass
    return made
