"""One command for the live partner's journey bench (docs/bench/voice-journeys.md): an isolated second stack (its own
home and ports, never the live app), the plan's setup once, N repetitions of the plan for one voice in a real Chromium,
each scored against the acceptance standard (standard.py), written as result.json and a short scoreboard.md.

  cd services && .venv/bin/python -m bench.voice_journeys.run --voice gpt-realtime-2.1 --talk hold \\
      --plan part-2.5 --reps 3 --out /tmp/p25-rt --budget <path to budget.py>
  cd services && .venv/bin/python -m bench.voice_journeys.run --voice gpt-live-1 --plan part-2.5 --reps 3 \\
      --out /tmp/p25-live --budget <path to budget.py>
  cd services && .venv/bin/python -m bench.voice_journeys.run --voice gemini-3.8-live --talk hold \\
      --plan part-2.5 --reps 1 --out /tmp/p25-gemini --budget <path to budget.py>
  cd services && .venv/bin/python -m bench.voice_journeys.run --score-only --out /tmp/p25-rt   # nothing started or spent
  cd services && .venv/bin/python -m bench.voice_journeys.compare /tmp/p25-live /tmp/p25-rt

Every paid run reserves its worst case first (`budget.py reserve pr <usd> <owner> <purpose>`) and refuses when the
reservation is refused; it settles afterwards from what the stack recorded (its Spending: /api/spending, plus the one
brain's provider-reported tokens when Spending leaves them unpriced, plus the judge), else at the worst case.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import stack as stacks
from .standard import markdown, meets, read_jsonl, realtime_cost, score_run, scoreboard, summarise

HERE = Path(__file__).resolve().parent
SERVICES = HERE.parents[1]
REPO = SERVICES.parent
VOICES = ("gpt-live-1", "gpt-realtime-2.1", "gemini-3.8-live")
ONE_BRAINS = ("gpt-realtime-2.1", "gemini-3.8-live")  # hold to talk by default; GPT-Live listens with an open mic
TALK = ("hold", "open")
OWNER = "p25-rt-bench"
# Worst cases, in dollars (PARTNER-CONTRACT.md's prices; GPT-Live's minutes plus the brain's calls): per spoken step,
# per researcher the plan starts (bounded by the stack's research cap), per setup (once a stack), per judged step.
# Gemini 3.8 Live: a turn re-reads its whole context (at most 64K tokens, mostly text and video at $0.75-1 per million)
# plus its audio; the smoke test's held turn with a tool cost $0.0064.
STEP_USD = {"gpt-realtime-2.1": 0.55, "gpt-live-1": 0.15, "gemini-3.8-live": 0.10}
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
    from .judge import COST_PER_STEP_USD

    per_run = spoken_steps(plan) * (STEP_USD[voice] + (COST_PER_STEP_USD if judge else 0)) + researchers(plan) * RESEARCHER_USD
    return round(per_run * reps + (SETUP_USD if setup and plan.get("setup") else 0), 2)


def reserve(budget: str, usd: float, purpose: str, owner: str = OWNER) -> str:
    """The reservation id, or SystemExit(3) when the budget refuses it (nothing is started)."""
    done = subprocess.run([sys.executable, budget, "reserve", "pr", f"{usd:.2f}", owner, purpose],
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


def write_results(out: Path, plan: dict, about: dict, *, logs: Path | None = None, judge: bool = False,
                  checkout: Path = REPO) -> dict:
    """Score every run folder in `out` (run-N/bench.json) against the stack's logs, judged when asked (verdicts kept in
    run-N/verdicts.json, so a re-score reuses them), and write result.json and scoreboard.md."""
    logs = logs or out / "home" / "logs"
    conv, inter = read_jsonl(logs / "conversation.jsonl"), read_jsonl(logs / "interactions.jsonl")
    setup = out / "setup" / "bench.json"  # its frames: the live view's baseline when a run has none before Talk
    baseline = [e for e in json.loads(setup.read_text()).get("events", []) if e.get("e") == "frames"] if setup.exists() else None
    runs, recovered, staged = [], [], 0
    for folder in runs_in(out):
        bench = json.loads((folder / "bench.json").read_text())
        kept = folder / "verdicts.json"
        verdicts = json.loads(kept.read_text()) if kept.exists() else None
        if verdicts is None and judge:
            from .judge import judge_run

            verdicts = judge_run(bench, conv, inter, plan, checkout)
            kept.write_text(json.dumps(verdicts, indent=1))
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
                        "audioFrom": "; ".join(heard.get(s, s) for s in sorted(sources)) or "nowhere: no runs"},
              "summary": summary, "scoreboard": board, "meets": meets(board), "recovered": recovered,
              "staged": staged, "runs": runs}
    (out / "result.json").write_text(json.dumps(result, indent=1))
    (out / "scoreboard.md").write_text(markdown(result))
    return result


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
    parser.add_argument("--owner", default=OWNER, help="who the budget's reservation is for")
    parser.add_argument("--no-judge", action="store_true", help="timings and counts only: false done not measured")
    parser.add_argument("--acoustic", action="store_true",
                        help="play the words through this Mac's speakers into its microphone (a visible browser)")
    parser.add_argument("--no-faults", action="store_true", help="don't inject the plan's faults (forced reconnect)")
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
        result = write_results(out, plan, about, judge=not args.no_judge, checkout=Path(args.checkout))
        print(markdown(result))
        return 0 if result["meets"] else 1
    if not args.voice:
        parser.error("--voice is required (unless --score-only)")
    talk = args.talk or ("hold" if args.voice in ONE_BRAINS else "open")
    if args.voice == "gpt-live-1" and talk == "hold":
        parser.error("GPT-Live has no hold to talk: it listens with an open microphone (--talk open)")
    if not args.budget:
        parser.error("a paid run needs --budget (budget.py) to reserve its worst case first; --score-only spends nothing")
    setup_needed = bool(plan.get("setup")) and not (out / "setup" / "bench.json").exists()
    worst = args.worst_usd if args.worst_usd is not None else \
        worst_case(plan, args.reps, args.voice, judge=not args.no_judge, setup=setup_needed)
    reservation = reserve(args.budget, worst, f"journey {plan['id']} x{args.reps} {args.voice}/{talk}", args.owner)
    checkout = Path(args.checkout).resolve()
    about = {"voice": args.voice, "talk": talk, "checkout": str(checkout), "commit": commit(checkout),
             "acoustic": args.acoustic, "reservation": reservation, "worstUsd": worst, "started": time.time(),
             "evidence": ("recorded audio played through this Mac's speakers into its microphone" if args.acoustic else
                          "recorded audio (macOS voices) injected as the microphone") +
                         ("; the camera was a still image (" + plan["camera"]["still"] + "), looped with sensor-like noise"
                          if (plan.get("camera") or {}).get("still") else "; the camera was test footage")}
    about_file.write_text(json.dumps(about, indent=1))
    up = argparse.Namespace(checkout=str(checkout), out=str(out), plan=args.plan, port=args.port,
                            bridge_port=args.bridge_port, keys=args.keys, build=False, settings="", voice=args.voice,
                            talk=talk if args.voice in ONE_BRAINS else "", faults=not args.no_faults,
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
                os.environ["MICROMIND_KEYS_FILE"] = args.keys  # the judge's own calls, this process only
                result = write_results(out, plan, about, judge=not args.no_judge, checkout=checkout)
        finally:
            from .judge import COST_PER_STEP_USD

            judged = 0 if args.no_judge else sum(len(r["steps"]) for r in (result or {}).get("runs", []))
            actual = worst if cost is None else round(cost + judged * COST_PER_STEP_USD, 2)
            settle(args.budget, reservation, actual, "from the stack's Spending" if cost is not None else "worst case")
    if result is not None:
        print(markdown(result))
        return 0 if result["meets"] else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
