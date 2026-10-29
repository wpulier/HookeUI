"""One command for the live partner's journey bench (docs/bench/voice-journeys.md): an isolated second stack (its own
home and ports, never the live app), the plan's setup once, N repetitions of the plan for one voice in a real Chromium,
each scored against the acceptance standard (standard.py), written as result.json and a short scoreboard.md.

  cd services && .venv/bin/python -m bench.voice_journeys.run --voice gpt-realtime-2.1 --talk hold \\
      --plan part-2.5 --reps 3 --out /tmp/p25-rt --budget <path to budget.py>
  cd services && .venv/bin/python -m bench.voice_journeys.run --voice gpt-live-1 --plan part-2.5 --reps 3 \\
      --out /tmp/p25-live --budget <path to budget.py>
  cd services && .venv/bin/python -m bench.voice_journeys.run --score-only --out /tmp/p25-rt   # nothing started or spent
  cd services && .venv/bin/python -m bench.voice_journeys.compare /tmp/p25-live /tmp/p25-rt

Every paid run reserves its worst case first (`budget.py reserve pr <usd> <owner> <purpose>`) and refuses when the
reservation is refused; it settles afterwards from what the stack recorded (its Spending: /api/spending, plus the one
brain's provider-reported tokens when Spending leaves them unpriced, plus the judge), else at the worst case.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import stack as stacks
from .judge import COST_PER_STEP_USD, JUDGES
from .standard import SCORER_VERSION, markdown, meets, read_jsonl, realtime_cost, score_run, scoreboard, summarise

HERE = Path(__file__).resolve().parent
SERVICES = HERE.parents[1]
REPO = SERVICES.parent
VOICES = ("gpt-live-1", "gpt-realtime-2.1")
TALK = ("hold", "open")
OWNER = "p25-rt-bench"
# Worst cases, in dollars (PARTNER-CONTRACT.md's prices; GPT-Live's minutes plus the brain's calls): per spoken step,
# per researcher the plan starts (bounded by the stack's research cap), per setup (once a stack), per judged step.
STEP_USD = {"gpt-realtime-2.1": 0.55, "gpt-live-1": 0.15}
RESEARCHER_USD = 1.50
SETUP_USD = 2.50
PLAYWRIGHT = Path.home() / ".npm/_npx/31e32ef8478fbf80/node_modules/playwright/index.mjs"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def spoken_steps(plan: dict) -> int:
    return sum(1 for s in plan["steps"] if s.get("kind") not in ("typed", "idle", "restart"))


def researchers(plan: dict) -> int:
    return sum(int((s.get("expect") or {}).get("researchers") or 0) for s in plan["steps"])


def worst_case(plan: dict, reps: int, voice: str, *, judge: bool, setup: bool) -> float:
    """The most this run can cost: every spoken step at its worst, every researcher at its cap, the setup, the judge."""
    per_run = spoken_steps(plan) * (STEP_USD[voice] + (COST_PER_STEP_USD if judge else 0)) + researchers(plan) * RESEARCHER_USD
    return round(per_run * reps + (SETUP_USD if setup and plan.get("setup") else 0), 2)


def reserve(budget: str, usd: float, purpose: str) -> str:
    """The reservation id, or SystemExit(3) when the budget refuses it (nothing is started)."""
    done = subprocess.run([sys.executable, budget, "reserve", "pr", f"{usd:.2f}", OWNER, purpose],
                          capture_output=True, text=True)
    if done.returncode != 0:
        print(f"refused by the budget: {done.stderr.strip() or done.stdout.strip()}", file=sys.stderr)
        raise SystemExit(3)
    return done.stdout.strip().splitlines()[-1]


def settle(budget: str, reservation: str, usd: float, note: str) -> None:
    subprocess.run([sys.executable, budget, "settle", reservation, f"{usd:.2f}", note], check=False)


def spent(port: int, inter: list[dict]) -> float | None:
    """What the stack recorded: its Spending (the month's counted balance, holds included; a fresh home has only this
    run), plus the one brain's provider-reported tokens priced when Spending has unpriced entries."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/spending", timeout=10) as r:
            spending = json.loads(r.read())
    except Exception:
        return None
    usd = max(float(spending.get("estimatedUsd") or 0), float((spending.get("month") or {}).get("estimatedUsd") or 0))
    tokens = realtime_cost(inter) or 0.0
    return round(usd + tokens if spending.get("unpriced") else max(usd, tokens), 4)


def browser() -> tuple[str, str]:
    playwright = os.environ.get("HOOKE_PLAYWRIGHT") or str(PLAYWRIGHT)
    chromium = os.environ.get("HOOKE_CHROMIUM") or str(CHROME)
    for what, path in (("HOOKE_PLAYWRIGHT (Playwright's index.mjs)", playwright), ("HOOKE_CHROMIUM", chromium)):
        if not Path(path).exists():
            raise SystemExit(f"{what} not found at {path}")
    return playwright, chromium


def drive(out: Path, plan: dict, told: dict, *, mode: str, voice: str, setup: bool, acoustic: bool, name: str) -> Path:
    """One pass of the plan (or its setup) in a fresh browser profile; returns the player's state file."""
    playwright, chromium = browser()
    profile = out / f"browser-{name}"
    shutil.rmtree(profile, ignore_errors=True)
    profile.mkdir(parents=True)
    folder = out / name
    folder.mkdir(parents=True, exist_ok=True)
    plan_file = folder / "plan.json"
    plan_file.write_text(json.dumps(plan))
    state = folder / "bench.json"
    options = {"mode": mode, "voice": voice, "setup": setup, "acoustic": acoustic, "bridge": told["bridge"],
               "restart": [sys.executable, "-m", "bench.voice_journeys.stack", "restart", "--out", str(out)],
               "cwd": str(SERVICES)}
    subprocess.run(["node", str(HERE / "drive.mjs"), playwright, chromium, str(profile), told["studio"], str(plan_file),
                    str(state), json.dumps(options)], check=True, timeout=4 * 3600)
    return state


def runs_in(out: Path) -> list[Path]:
    return sorted((p for p in out.glob("run-*") if (p / "bench.json").exists()), key=lambda p: int(p.name.split("-")[1]))


def to_judge(out: Path, *, judge: bool, rejudge: bool = False) -> list[Path]:
    """The run folders the judge must read: none kept, a kept one with a step the judge could not answer (its credit
    ran out), or all when asked to judge again."""
    if not judge:
        return []
    folders = []
    for folder in runs_in(out):
        kept = folder / "verdicts.json"
        verdicts = json.loads(kept.read_text()) if kept.exists() else None
        if rejudge or verdicts is None or unjudged(folder):
            folders.append(folder)
    return folders


def unjudged(folder: Path, rejudge: bool = False) -> set:
    """The steps of a run the judge must read: all when asked again or none kept, else those kept without an answer
    (deleting a step's verdict from verdicts.json judges just that step again)."""
    bench = json.loads((folder / "bench.json").read_text())
    ids = {s["id"] for s in bench.get("steps") or [] if not s.get("setup") and s.get("kind") not in ("idle", "restart")}
    kept = folder / "verdicts.json"
    verdicts = json.loads(kept.read_text()) if kept.exists() else None
    if rejudge or verdicts is None:
        return ids
    return {i for i in ids if not isinstance(verdicts.get(i), dict) or verdicts[i].get("error")}


def judged_steps(folder: Path, rejudge: bool = False) -> int:
    return len(unjudged(folder, rejudge))


def write_results(out: Path, plan: dict, about: dict, *, logs: Path | None = None, judge: bool = False,
                  checkout: Path = REPO, judge_name: str = "auto", rejudge: bool = False) -> dict:
    """Score every run folder in `out` (run-N/bench.json) against the stack's logs, judged when asked (verdicts kept in
    run-N/verdicts.json with the judge's name, so a re-score reuses them), and write result.json and scoreboard.md."""
    logs = logs or out / "home" / "logs"
    conv, inter = read_jsonl(logs / "conversation.jsonl"), read_jsonl(logs / "interactions.jsonl")
    setup = out / "setup" / "bench.json"  # its frames: the live view's baseline when a run has none before Talk
    baseline = [e for e in json.loads(setup.read_text()).get("events", []) if e.get("e") == "frames"] if setup.exists() else None
    runs, recovered, staged, judges, passed_over = [], [], 0, [], []
    pending = set(to_judge(out, judge=judge, rejudge=rejudge))
    chosen = None
    if pending:
        from .judge import choose

        chosen, _ = choose(judge_name, passed_over)
        if chosen is None:
            print("no judge answers now: " + "; ".join(passed_over), file=sys.stderr)
    for folder in runs_in(out):
        bench = json.loads((folder / "bench.json").read_text())
        kept = folder / "verdicts.json"
        verdicts = json.loads(kept.read_text()) if kept.exists() else None
        if folder in pending and chosen is not None:
            from .judge import judge_run

            fresh = judge_run(bench, conv, inter, plan, checkout, name=chosen, only=unjudged(folder, rejudge))
            old = verdicts or {}
            still = [k for k, v in old.items() if not k.startswith("_") and k not in fresh and isinstance(v, dict)
                     and not v.get("error")]
            if still and old.get("_judge") and old["_judge"] != chosen:  # a run judged by two judges says so
                fresh["_judge"] = f"{old['_judge']} + {chosen}"
            verdicts = {**old, **fresh}
            kept.write_text(json.dumps(verdicts, indent=1))
        from .judge import judged_by

        judges.append(judged_by(verdicts) or "none")
        runs.append(score_run(bench, conv, inter, plan, verdicts, baseline))
        recovered += [{"run": folder.name, **{k: e.get(k) for k in ("step", "what", "from", "ok")}}
                      for e in bench.get("events", []) if e.get("e") == "recovered"]
        staged += sum(1 for e in bench.get("events", []) if e.get("e") == "staged" and e.get("step") != "start")
    summary = summarise(runs)
    board = scoreboard(summary, about.get("voice"))
    sources = {r["audioFrom"] for r in runs}
    heard = {"page": "in the page", "server": "from the one brain's audio records",
             "transcripts": "from the transcripts' first words (no audio timing)"}
    result = {"about": {**about, "plan": plan["id"], "reps": len(runs),
                        "audioFrom": "; ".join(heard.get(s, s) for s in sorted(sources)) or "nowhere: no runs",
                        "judge": ", ".join(dict.fromkeys(judges)) or "none", "scorer": SCORER_VERSION,
                        **({"judgesPassedOver": passed_over} if passed_over else {})},
              "summary": summary, "scoreboard": board, "meets": meets(board), "recovered": recovered,
              "staged": staged, "runs": runs}
    (out / "result.json").write_text(json.dumps(result, indent=1))
    (out / "scoreboard.md").write_text(markdown(result))
    return result


@contextlib.contextmanager
def judge_keys(path: str):
    """The keys file for the judge's own calls while they run, and the process environment as it was afterwards (the
    keys the judge loads included): a run never leaves another keys file behind for what runs next in the process
    (a test worker read another capabilities wire, 2026-10-29)."""
    saved = dict(os.environ)
    os.environ["MICROMIND_KEYS_FILE"] = path
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def commit(checkout: Path) -> str:
    done = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=checkout, capture_output=True, text=True)
    return done.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voice", choices=VOICES, help="the voice option this run uses")
    parser.add_argument("--talk", choices=TALK, help="hold (the one brain's default) or open; GPT-Live is open only")
    parser.add_argument("--plan", default="part-2.5", help="plans/<id>.json, or a path")
    parser.add_argument("--reps", type=int, default=1, help="repetitions of the plan, in a row, on one stack")
    parser.add_argument("--out", required=True, help="this run's folder: the stack's home, each run, the results")
    parser.add_argument("--checkout", default=str(REPO), help="the checkout the stack runs (default: this one)")
    parser.add_argument("--keys", default=str(stacks.KEYS), help="the keys file the stack (and the judge) read")
    parser.add_argument("--budget", default=os.environ.get("HOOKE_BENCH_BUDGET", ""), help="budget.py, for reserve/settle")
    parser.add_argument("--worst-usd", type=float, help="reserve this instead of the computed worst case")
    parser.add_argument("--no-judge", action="store_true", help="timings and counts only: false done not measured")
    parser.add_argument("--judge", default="auto", choices=("auto", *JUDGES),
                        help="the judge: auto = gpt-6.1-sol (the default, comparable with old runs), else claude-opus-5-5, "
                             "else gemini-3.8-flash, the first whose key and credit work now")
    parser.add_argument("--rejudge", action="store_true", help="judge kept runs again (the judge used is recorded)")
    parser.add_argument("--acoustic", action="store_true",
                        help="play the words through this Mac's speakers into its microphone (a visible browser)")
    parser.add_argument("--no-faults", action="store_true", help="don't inject the plan's faults (forced reconnect)")
    parser.add_argument("--settings", default="", help='settings for the stack, JSON over the plan\'s "settings" '
                        '(e.g. {"vision": "claude-opus-5-5-fast", "think": "claude-opus-5-5"})')
    parser.add_argument("--port", type=int, default=28870)
    parser.add_argument("--bridge-port", type=int, default=28865)
    parser.add_argument("--keep-up", action="store_true", help="leave the stack running afterwards")
    parser.add_argument("--score-only", action="store_true", help="re-score the run folders in --out; nothing started")
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    plan = stacks.load_plan(args.plan)
    about_file = out / "about.json"
    if args.score_only:
        about = json.loads(about_file.read_text()) if about_file.exists() else {}
        judging = to_judge(out, judge=not args.no_judge, rejudge=args.rejudge)
        reservation = None
        if judging:  # the judge's calls are paid: reserved first, refused when the budget refuses
            if not args.budget:
                parser.error(f"{len(judging)} run(s) need the judge: pass --budget to reserve it, or --no-judge")
            worst = round(sum(judged_steps(f, args.rejudge) for f in judging) * COST_PER_STEP_USD, 2)
            reservation = reserve(args.budget, worst, f"judge {plan['id']} x{len(judging)}")
        try:
            with judge_keys(args.keys):
                result = write_results(out, plan, about, judge=not args.no_judge, checkout=Path(args.checkout),
                                       judge_name=args.judge, rejudge=args.rejudge)
        finally:
            if reservation:
                settle(args.budget, reservation, worst, "judge: worst case")
        print(markdown(result))
        return 0 if result["meets"] else 1
    if not args.voice:
        parser.error("--voice is required (unless --score-only)")
    talk = args.talk or ("hold" if args.voice == "gpt-realtime-2.1" else "open")
    if args.voice == "gpt-live-1" and talk == "hold":
        parser.error("GPT-Live has no hold to talk: it listens with an open microphone (--talk open)")
    if not args.budget:
        parser.error("a paid run needs --budget (budget.py) to reserve its worst case first; --score-only spends nothing")
    setup_needed = bool(plan.get("setup")) and not (out / "setup" / "bench.json").exists()
    worst = args.worst_usd if args.worst_usd is not None else \
        worst_case(plan, args.reps, args.voice, judge=not args.no_judge, setup=setup_needed)
    reservation = reserve(args.budget, worst, f"journey {plan['id']} x{args.reps} {args.voice}/{talk}")
    checkout = Path(args.checkout).resolve()
    about = {"voice": args.voice, "talk": talk, "settings": {**(plan.get("settings") or {}),
                                                              **json.loads(args.settings or "{}")}, "checkout": str(checkout), "commit": commit(checkout),
             "acoustic": args.acoustic, "reservation": reservation, "worstUsd": worst, "started": time.time(),
             "evidence": ("recorded audio played through this Mac's speakers into its microphone" if args.acoustic else
                          "recorded audio (macOS voices) injected as the microphone") +
                         ("; the camera was a still image (" + plan["camera"]["still"] + "), looped with sensor-like noise"
                          if (plan.get("camera") or {}).get("still") else "; the camera was test footage")}
    about_file.write_text(json.dumps(about, indent=1))
    up = argparse.Namespace(checkout=str(checkout), out=str(out), plan=args.plan, port=args.port,
                            bridge_port=args.bridge_port, keys=args.keys, build=False,
                            settings=json.dumps({**(plan.get("settings") or {}), **json.loads(args.settings or "{}")})
                            if plan.get("settings") or args.settings else "", voice=args.voice,
                            talk=talk if args.voice == "gpt-realtime-2.1" else "", faults=not args.no_faults,
                            research_cap=RESEARCHER_USD * (researchers(plan) * args.reps + int(setup_needed)))
    cost = None
    try:
        told = stacks.up(up)
        if not told["ok"]:
            raise SystemExit(f"the stack did not become healthy: see {out / 'orchestrator.log'}")
        if setup_needed:
            drive(out, plan, told, mode=talk, voice=args.voice, setup=True, acoustic=False, name="setup")
        first = len(runs_in(out)) + 1
        for i in range(first, first + args.reps):
            drive(out, plan, told, mode=talk, voice=args.voice, setup=False, acoustic=args.acoustic, name=f"run-{i}")
        cost = spent(args.port, read_jsonl(out / "home" / "logs" / "interactions.jsonl"))
    finally:
        if not args.keep_up:
            stacks.down(argparse.Namespace(out=str(out)))
        result = None
        try:
            if runs_in(out):
                with judge_keys(args.keys):
                    result = write_results(out, plan, about, judge=not args.no_judge, checkout=checkout,
                                           judge_name=args.judge)
        finally:
            judged = 0 if args.no_judge else sum(len(r["steps"]) for r in (result or {}).get("runs", []))
            actual = worst if cost is None else round(cost + judged * COST_PER_STEP_USD, 2)
            settle(args.budget, reservation, actual, "from the stack's Spending" if cost is not None else "worst case")
    if result is not None:
        print(markdown(result))
        return 0 if result["meets"] else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
