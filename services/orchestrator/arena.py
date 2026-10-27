"""Blind A/B/C trials: several options answer the same click or question on the same frame,
side by side, and the user picks the best before learning which option was which.

  outline trial  one click, outlined by each selected outline model
  ask trial      one question, answered by each selected vision model + pointing strategy
                 (all outlined with the current outline model, so only the model differs);
                 an arm may add a level ("gemini-3.8-flash/box/technical"): the same model told
                 that level's line (learner.py), so wordings are compared blind; an arm without
                 one is told no level, as before levels existed

Letters are assigned by a shuffle per trial, so position never gives an option away. Arms
land in the scene tagged with their trial and letter (locked and faded by the usual rules)
and are announced as they finish; option names and timings stay hidden until the vote.
Votes are appended to services/logs/votes.jsonl for services/bench/arena_report.py.

Each trial says its policy (ADR 0011): "route" (the default: each arm with its declared fallbacks,
as users get it) or "raw" (each arm exactly that model, or it fails). Votes record the policy and
what each arm really ran; scores are never pooled across policies, nor with votes from before
policies were recorded ("route-legacy"), and defaults are recommended from "route" votes only.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import tools, execution
from .agent import MAX_TARGETS
from .learner import LEVELS, line as learner_line
from .options import GROUPS, POINTING, VISION
from .paths import Paths

LETTERS = "ABCD"
POLICIES = ("route", "raw")
WINNERS = (*LETTERS, "tie", "none")
DEFAULT_ARMS = {
    "outline": ["edgetam", "sam2.1-tiny", "edgetam>sam3.1-fal"],
    "ask": ["gemini-3.8-flash/box", "gemini-3.5-flash-lite/box", "gpt-6-luna/box"],
}


def ask_arm_ids() -> list[str]:
    # snap-first only adds an early ring to the box strategy, so it isn't a separate arm.
    # Only options this edition may use (orchestrator/licences.py) are offered as arms.
    return [f"{v}/{p}" for v in VISION for p in POINTING if p != "snap-first" and VISION[v].permitted and POINTING[p].permitted]


def level_arm_ids(vision: str, pointing: str = "box") -> list[str]:
    """One model and pointing told each level (learner.py): the wording, compared blind."""
    return [f"{vision}/{pointing}/{level}" for level in LEVELS]


def ask_arm(option: str) -> tuple[str, str, str]:
    """(vision, pointing, level or "") of an ask arm's id."""
    vision, pointing, *level = option.split("/")
    return vision, pointing, level[0] if level else ""


def arm_label(kind: str, option: str) -> str:
    if kind == "outline":
        return GROUPS["outline"][option].label
    vision, pointing, level = ask_arm(option)
    return f"{VISION[vision].label}, {POINTING[pointing].label.lower()}" + (f", {level} wording" if level else "")


@dataclass
class Arm:
    letter: str
    option: str
    done: bool = False
    ms: float | None = None
    error: str | None = None
    object_ids: list[str] = field(default_factory=list)
    answer: str = ""
    labels: list[str] = field(default_factory=list)
    executed: str | None = None  # what really ran (a route's declared fallback can differ from the option)
    executions: list[str] = field(default_factory=list)  # the runner's executions behind its outlines (revealed by the vote)

    def blind(self) -> dict:
        # Error texts can name the model, so only say that this arm failed.
        return {"letter": self.letter, "done": self.done, "failed": self.error is not None, "objectIds": self.object_ids,
                "answer": self.answer, "labels": self.labels}


@dataclass
class Trial:
    id: str
    kind: str  # "outline" | "ask"
    input: dict
    frame_id: int
    arms: dict[str, Arm]
    started: float = field(default_factory=time.time)
    winner: str | None = None
    note: str = ""
    policy: str = "route"

    def blind(self) -> dict:
        return {"id": self.id, "kind": self.kind, "input": self.input, "frameId": self.frame_id, "policy": self.policy,
                "arms": [a.blind() for a in self.arms.values()], "voted": self.winner is not None}

    def reveal(self) -> dict:
        return {letter: {"option": a.option, "label": arm_label(self.kind, a.option),
                         "ms": None if a.ms is None else round(a.ms), "error": a.error}
                for letter, a in self.arms.items()}


class Arena:
    def __init__(self, hub, agent, rng: random.Random | None = None, votes_log: Path | None = None) -> None:
        self.hub = hub
        self.agent = agent
        self.rng = rng or random.Random()
        # A test stack (the sim harness) keeps its own, so defaults are chosen from real votes only.
        self.votes_log = Path(votes_log) if votes_log else Paths.from_env().votes_log
        self.enabled = False
        self.policy = "route"  # each arm as users get it (with its declared fallbacks), or "raw"
        self.arms = {k: list(v) for k, v in DEFAULT_ARMS.items()}
        self.trials: dict[str, Trial] = {}
        self.current: Trial | None = None
        self._ids = 0
        self.taps: list = []  # (vote record) -> None, after it is written

    # --- configuration -----------------------------------------------------------------
    def configure(self, changes: dict) -> list[str]:
        problems = []
        if "enabled" in changes:
            self.enabled = bool(changes["enabled"])
            if not self.enabled:
                self.discard_current()
        if "policy" in changes:
            if changes["policy"] in POLICIES:
                self.policy = changes["policy"]
            else:
                problems.append(f"policy is one of {', '.join(POLICIES)}")
        asks = ask_arm_ids()
        valid = {"outline": {o for o, one in GROUPS["outline"].items() if one.permitted},
                 "ask": {*asks, *(f"{o}/{level}" for o in asks for level in LEVELS)}}
        for kind in ("outline", "ask"):
            if kind not in changes:
                continue
            chosen = [o for o in dict.fromkeys(changes[kind] or []) if o in valid[kind]]
            unknown = [o for o in changes[kind] or [] if o not in valid[kind]]
            if unknown:
                problems.append(f"unknown {kind} arms: {', '.join(map(str, unknown))}")
            if not 2 <= len(chosen) <= len(LETTERS):
                problems.append(f"pick 2 to {len(LETTERS)} {kind} options to compare")
                continue
            self.arms[kind] = chosen
        return problems

    def state(self) -> dict:
        return {"enabled": self.enabled, "arms": self.arms, "policy": getattr(self, "policy", "route"),
                "choices": {"outline": [{"id": o, "label": one.label} for o, one in GROUPS["outline"].items() if one.permitted],
                            "ask": [{"id": o, "label": arm_label("ask", o)} for o in self._ask_choices()]},
                "trial": self.current.blind() if self.current else None}

    def _ask_choices(self) -> list[str]:
        """Every model and pointing, plus the current model told each level (any model's levels
        can be configured; offering all of them would double a long list)."""
        vision = getattr(getattr(getattr(self, "hub", None), "settings", None), "vision", "")
        return ask_arm_ids() + (level_arm_ids(vision) if vision in VISION and VISION[vision].permitted else [])

    # --- trials ------------------------------------------------------------------------
    def _new_trial(self, kind: str, input: dict, frame_id: int) -> Trial:
        self.discard_current()
        self._ids += 1
        options = list(self.arms[kind])
        self.rng.shuffle(options)
        arms = {LETTERS[i]: Arm(LETTERS[i], option) for i, option in enumerate(options)}
        # An outline trial's arms are outline implementations, run by the trial's policy. An ask trial's
        # arms are vision models (not operations): the outlines each arm points at run as users get
        # them, by route, and its votes say so (scaffolding review, M1.2 item 9).
        trial = Trial(f"t{self._ids}", kind, input, frame_id, arms, policy=self.policy if kind == "outline" else "route")
        execution.bind(trial_id=trial.id)
        self.trials[trial.id] = trial
        self.current = trial
        return trial

    def discard_current(self) -> None:
        """Drop an unvoted trial's outlines (a new trial or leaving arena mode)."""
        trial = self.current
        if trial is not None and trial.winner is None:
            for obj in [o for o in self.hub.scene.objects.values() if o.trial == trial.id]:
                self.hub.scene.remove(obj.id)
        self.current = None

    def _announce(self, trial: Trial) -> None:
        self.hub.broadcast({"type": "trial", "trial": trial.blind()})

    async def outline_trial(self, frame_id: int, x: float, y: float,
                            box: tuple[float, float, float, float] | None = None) -> Trial:
        """Every arm outlines the same click (or the same dragged box)."""
        params = {"x": round(x, 1), "y": round(y, 1)}
        if box is not None:
            params["box"] = [round(v, 1) for v in box]
        trial = self._new_trial("outline", params, frame_id)
        self._announce(trial)
        # Every arm outlines the same view: one capture, held for the trial (ADR 0010).
        from .capture import captures

        capture = captures(self.hub).get(frame_id)
        hold = captures(self.hub).hold(capture, "interactive", "outline", f"arena {trial.id}") if capture is not None else None
        view = capture if capture is not None else frame_id

        @execution.traced("arena-arm")
        async def run(arm: Arm) -> None:
            execution.note(selected=arm.option, arm=arm.letter, policy=trial.policy)
            t0 = time.perf_counter()
            try:
                obj = await tools.outline_at(self.hub, view, x, y, box=box, source="arena", model=arm.option,
                                             policy=trial.policy, origin="arena", trial=trial.id, arm=arm.letter)
                arm.object_ids = [obj.id]
                from .operations import runner

                ran = runner(self.hub).get((obj.made_by or {}).get("execution"))
                arm.executed = ran.executed if ran is not None else None  # kept here, revealed by the vote
                arm.executions = [ran.id] if ran is not None else []
            except Exception as exc:
                execution.finish("failed", errorType=type(exc).__name__)
                arm.error = str(exc) or type(exc).__name__
            arm.ms = (time.perf_counter() - t0) * 1000
            arm.done = True
            if self.current is trial:
                self._announce(trial)

        try:
            await asyncio.gather(*(run(a) for a in trial.arms.values()))
        finally:
            if hold is not None:
                hold.release()
        return trial

    async def ask_trial(self, question: str) -> Trial:
        view = self.agent.snapshot()
        info, _, crop = view
        trial = self._new_trial("ask", {"question": question}, info.id)
        self._announce(trial)

        @execution.traced("arena-arm")
        async def run(arm: Arm) -> None:
            execution.note(selected=arm.option, arm=arm.letter, policy=trial.policy)
            vision, pointing, level = ask_arm(arm.option)
            # A level arm is told that level's line, with today's hints-first choice.
            learner = learner_line(level, bool(getattr(self.hub.settings, "hints_first", False))) if level else ""
            t0 = time.perf_counter()
            try:
                if not self.agent.available(vision):
                    raise tools.ToolError(f"no key for {VISION[vision].label}",
                                          next="turn the blind test off, or add that model's key and restart")
                data, used = await self.agent.look(question, view, option=vision, pointing=pointing, learner=learner)
                arm.answer = str(data.get("answer") or "").strip()
                targets = [t for t in (data.get("targets") or []) if isinstance(t, dict)][:MAX_TARGETS]
                if self.current is trial:
                    objects = await self.agent.point(targets, info, crop, used.get("marks"), used["model"],
                                                     trial=trial.id, arm=arm.letter)
                    arm.object_ids = [o.id for o in objects]
                    arm.labels = [o.label for o in objects]
                    arm.executions = [x for x in ((o.made_by or {}).get("execution") for o in objects) if x]
            except Exception as exc:
                execution.finish("failed", errorType=type(exc).__name__)
                arm.error = str(exc) or type(exc).__name__
            arm.ms = (time.perf_counter() - t0) * 1000
            arm.done = True
            if self.current is trial:
                self._announce(trial)

        await asyncio.gather(*(run(a) for a in trial.arms.values()))
        return trial

    # --- votes -------------------------------------------------------------------------
    def vote(self, trial_id: str, winner: str, note: str = "", by: str = "studio") -> dict:
        trial = self.trials.get(trial_id)
        if trial is None:
            raise tools.ToolError("that comparison is gone; start a new one", next=None)  # no next: the message says what to do
        if trial.winner is not None:
            raise tools.ToolError("that comparison already has a vote", next="start a new comparison to vote again")
        if winner not in WINNERS or (winner in LETTERS and winner not in trial.arms):
            raise tools.ToolError(f"vote for one of {', '.join(trial.arms)}, tie or none",
                                  next=None)  # no next: the message says what to do
        trial.winner, trial.note = winner, note.strip()[:500]
        # Keep the winner's outlines as ordinary scene objects; the rest go.
        from .operations import runner

        for obj in [o for o in self.hub.scene.objects.values() if o.trial == trial.id]:
            if obj.arm == winner:
                ran = runner(self.hub).get((obj.made_by or {}).get("execution"))
                self.hub.scene.update(obj.id, trial=None, arm=None, made_by=ran.made_by() if ran is not None else obj.made_by)
            else:
                self.hub.scene.remove(obj.id)
        reveal = trial.reveal()
        for arm in trial.arms.values():  # the interaction log's blind execution lines, now in full
            for execution_id in arm.executions:
                runner(self.hub).reveal(execution_id)
        log = getattr(self.hub, "execution", None)
        if log is not None:
            log.reveal(trial.id)
        record = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "trial": trial.id,
            "kind": trial.kind,
            "input": trial.input,
            "frame": trial.frame_id,
            "specimen": self.hub.scene.context.get("specimen"),
            "arms": {letter: r["option"] for letter, r in reveal.items()},
            "ms": {letter: r["ms"] for letter, r in reveal.items()},
            "errors": {letter: r["error"] for letter, r in reveal.items() if r["error"]},
            "winner": winner,
            "winnerOption": trial.arms[winner].option if winner in trial.arms else None,
            "policy": trial.policy,  # never pooled across policies (ADR 0011)
            "executed": {letter: a.executed for letter, a in trial.arms.items() if a.executed},
            "executions": {letter: a.executions for letter, a in trial.arms.items() if a.executions},  # linkable outcomes
            "note": trial.note,
            "by": by,  # "studio" for a person's vote; test harnesses mark theirs so reports skip them
        }
        self.votes_log.parent.mkdir(parents=True, exist_ok=True)
        with self.votes_log.open("a") as f:
            f.write(json.dumps(record) + "\n")
        for tap in list(self.taps):  # a dataset being collected (datasets/collector.py)
            try:
                tap(record)
            except Exception:
                pass
        if self.current is trial:
            self.current = None
        return {"trialId": trial.id, "winner": winner, "reveal": reveal}


# --- scores ----------------------------------------------------------------------------
# Shared by the studio's Scores panel and services/bench/arena_report.py.
MIN_TRIALS = 3


def load_votes(path: Path | None = None) -> list[dict]:
    """A person's votes; test harnesses mark theirs (by != "studio") and are skipped."""
    path = Path(path) if path else Paths.from_env().votes_log
    if not path.exists():
        return []
    votes = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [v for v in votes if v.get("by", "studio") == "studio"]


def vote_policy(vote: dict) -> str:
    """A vote's policy: recorded since ADR 0011; earlier votes are "route-legacy", kept apart."""
    return vote.get("policy") or "route-legacy"


def score(votes: list[dict], policy: str | None = None) -> dict[str, dict[str, dict]]:
    """Rows per kind and option, from votes of one policy only (`policy`; None: each option keyed
    `option@policy`, so no row ever pools policies)."""
    table: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"trials": 0, "wins": 0, "ties": 0, "none": 0, "failed": 0, "ms": []}))
    for v in votes:
        if policy is not None and vote_policy(v) != policy:
            continue
        for letter, option in v["arms"].items():
            row = table[v["kind"]][option if policy is not None else f"{option}@{vote_policy(v)}"]
            row["trials"] += 1
            row["wins"] += v["winner"] == letter
            row["ties"] += v["winner"] == "tie"
            row["none"] += v["winner"] == "none"
            row["failed"] += letter in (v.get("errors") or {})
            ms = (v.get("ms") or {}).get(letter)
            if ms is not None and letter not in (v.get("errors") or {}):
                row["ms"].append(ms)
    return table


def recommend(rows: dict[str, dict], kind: str = "") -> str | None:
    """Best win rate among options seen MIN_TRIALS times; within 10 points, the faster one."""
    seen = {o: r for o, r in rows.items() if r["trials"] >= MIN_TRIALS}
    if not seen:
        return None
    rate = {o: r["wins"] / r["trials"] for o, r in seen.items()}
    best = max(rate.values())
    close = [o for o in seen if rate[o] >= best - 0.10]
    return min(close, key=lambda o: np.median(seen[o]["ms"]) if seen[o]["ms"] else float("inf"))


def scoreboard(votes: list[dict] | None = None, path: Path | None = None) -> dict:
    """The scores as data for the studio (the votes given, or those saved at `path`): one row per
    option and policy, never pooled; the recommendation comes from "route" votes only (what users get)."""
    votes = load_votes(path) if votes is None else votes
    table = score(votes)
    kinds = []
    words = {"route": "with its fallbacks", "raw": "the model alone", "route-legacy": "before policies were recorded"}
    for kind in ("outline", "ask"):
        rows = table.get(kind)
        if not rows:
            continue
        route = {key.rsplit("@", 1)[0]: r for key, r in rows.items() if key.endswith("@route")}
        pick = recommend(route, kind)
        options = []
        for key, r in sorted(rows.items(), key=lambda kv: -kv[1]["wins"] / kv[1]["trials"]):
            option, policy = key.rsplit("@", 1)
            options.append({
                "option": option,
                "policy": policy,
                "label": f"{arm_label(kind, option)} ({words.get(policy, policy)})",
                "trials": r["trials"],
                "wins": r["wins"],
                "ties": r["ties"],
                "none": r["none"],
                "failed": r["failed"],
                "medianMs": None if not r["ms"] else round(float(np.median(r["ms"]))),
                "recommended": option == pick and policy == "route",
            })
        kinds.append({"kind": kind, "votes": sum(v["kind"] == kind for v in votes), "options": options,
                      "recommended": pick, "minTrials": MIN_TRIALS})
    return {"votes": len(votes), "kinds": kinds}
