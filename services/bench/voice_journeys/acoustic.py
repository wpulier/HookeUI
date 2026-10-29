"""Acoustic replay against the live app (docs/bench/voice-journeys.md "Acoustic replay"): the plan's recorded words
played through this Mac's speakers into its microphone, with the talk key held through System Events in hold mode,
then scored from that app's own logs. It checks what injected audio can't: echo, the voice hearing itself, the room.

Needs a real microphone and speakers (not headphones), the volume up, Hooke in front with the Project open and Talk
connected, and Accessibility permission for the terminal running this (System Events presses the keys). A restart step
asks the person to quit and reopen Hooke and press Return. Setup steps are not typed: prepare the Project first.
Nothing is read from the page, so first audio and silences come from the server's own records (partner-response), and
marks, frames and the researchers board are not measured. It never starts or stops the app.

  cd services && .venv/bin/python -m bench.voice_journeys.acoustic --plan part-2.5 --home ~/MicroscopeMind \\
      --out /tmp/p25-acoustic --talk hold --budget <path to budget.py>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import stack as stacks
from .run import STEP_USD, reserve, settle, write_results

KEY_DOWN = 'tell application "System Events"\n key down control\n key down option\nend tell'
KEY_UP = 'tell application "System Events"\n key up option\n key up control\nend tell'


def press(down: bool) -> float:
    subprocess.run(["osascript", "-e", KEY_DOWN if down else KEY_UP], check=True)
    return time.time() * 1000


def quiet(log: Path, seconds: float, at_most: float, at_least: float = 6.0) -> None:
    """Until the app's conversation log has not grown for `seconds` (read only), at most `at_most`."""
    t0, size, changed = time.monotonic(), -1, time.monotonic()
    while time.monotonic() - t0 < at_most:
        now = log.stat().st_size if log.exists() else 0
        if now != size:
            size, changed = now, time.monotonic()
        if time.monotonic() - t0 >= at_least and time.monotonic() - changed >= seconds:
            return
        time.sleep(0.25)


def replay(plan: dict, words: Path, logs: Path, talk: str) -> dict:
    steps, events = [], []
    print("Bring Hooke to the front, with Talk connected. Starting in 5 s.", flush=True)
    time.sleep(5)
    for step in plan["steps"]:
        if step["kind"] == "typed":
            continue
        if step["kind"] == "restart":
            input("Quit and reopen Hooke, open the Project, connect Talk, then press Return. ")
            at = time.time() * 1000
            steps.append({"id": step["id"], "kind": "restart", "say": "", "start": at, "end": at, "restart": True})
            continue
        if step["kind"] == "idle":
            at = time.time() * 1000
            time.sleep(min(step.get("wait", 60), 90))
            steps.append({"id": step["id"], "kind": "idle", "say": "", "start": at, "end": time.time() * 1000, "idle": True})
            continue
        record = {"id": step["id"], "kind": step["kind"], "say": step["say"]}
        if step.get("interrupt"):
            record["interrupt"] = True
        if talk == "hold":
            record["down"] = press(True)
            time.sleep(0.15)
        record["start"] = time.time() * 1000
        subprocess.run(["afplay", str(words / f"{step['id']}.wav")], check=True)
        record["end"] = time.time() * 1000
        if talk == "hold":
            time.sleep(0.2)
            record["up"] = press(False)
        steps.append(record)
        following = plan["steps"].index(step) + 1
        if following < len(plan["steps"]) and plan["steps"][following].get("interrupt"):
            time.sleep((plan["steps"][following]["interrupt"].get("afterMs", 2000) + 1500) / 1000)  # into the answer
            continue
        quiet(logs / "conversation.jsonl", 6, step.get("wait", 30))
    return {"plan": plan["id"], "talk": talk, "acoustic": True, "steps": steps, "events": events}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", default="part-2.5")
    parser.add_argument("--home", required=True, help="the running app's home (its logs are read, never written)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--talk", choices=("hold", "open"), default="hold")
    parser.add_argument("--voice", default="gpt-realtime-2.1", choices=tuple(STEP_USD))
    parser.add_argument("--budget", default=os.environ.get("HOOKE_BENCH_BUDGET", ""))
    parser.add_argument("--no-judge", action="store_true")
    args = parser.parse_args()
    if not args.budget:
        parser.error("a paid run needs --budget (budget.py) to reserve its worst case first")
    plan = stacks.load_plan(args.plan)
    out = Path(args.out).resolve()
    words = out / "words"
    stacks.words(plan, words)
    worst = round(sum(1 for s in plan["steps"] if s["kind"] not in ("typed", "idle", "restart")) * STEP_USD[args.voice]
                  + 1.5 * sum(int((s.get("expect") or {}).get("researchers") or 0) for s in plan["steps"]), 2)
    reservation = reserve(args.budget, worst, f"acoustic {plan['id']} {args.voice}/{args.talk}")
    logs = Path(args.home).expanduser() / "logs"
    n = len(list(out.glob("run-*"))) + 1
    folder = out / f"run-{n}"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        state = replay(plan, words, logs, args.talk)
        (folder / "bench.json").write_text(json.dumps(state))
    finally:
        settle(args.budget, reservation, worst, "acoustic replay: worst case (the live app's Spending is the person's)")
    about = {"voice": args.voice, "talk": args.talk, "acoustic": True, "home": str(Path(args.home).expanduser()),
             "evidence": "recorded audio played through this Mac's speakers into its microphone, live app"}
    (out / "about.json").write_text(json.dumps(about, indent=1))
    result = write_results(out, plan, about, logs=logs, judge=not args.no_judge)
    print((out / "scoreboard.md").read_text())
    sys.exit(0 if result["meets"] else 1)


if __name__ == "__main__":
    main()
