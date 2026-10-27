"""A day at a glance: its conversations, researchers (with versions) and spend, and how fast
the voice was.

  make day-report            (or: cd services && .venv/bin/python -m bench.day_report [--day 2026-10-05])
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

from orchestrator.day import DayStore
from orchestrator.paths import Paths


def hm(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=time.strftime("%Y-%m-%d"))
    args = parser.parse_args()
    paths = Paths.from_env()  # MICROMIND_HOME and the per-folder overrides, as the app uses them
    journal = paths.sessions / args.day / "journal.jsonl"
    if not journal.exists():
        print(f"No journal for {args.day} yet ({journal}).")
    else:
        store = DayStore(root=paths.sessions, day=args.day)
        print(f"{args.day}: {len(store.threads)} conversation(s); specimens: {', '.join(store.subjects) or 'none'}")
        for t in sorted(store.threads.values(), key=lambda t: t.n):
            asked = sum(1 for x in t.turns if x.role == "user")
            minutes = (t.last - t.started) / 60
            print(f'  {t.id} "{t.title or "untitled"}" {hm(t.started)}-{hm(t.last)} ({minutes:.0f} min), {asked} requests'
                  + (f"; looked at {', '.join(s['name'] for s in t.subjects)}" if t.subjects else "")
                  + (f"; researchers {', '.join(t.research)}" if t.research else "")
                  + (" ; summarised" if t.summary else ""))
        if store.prefs:
            print(f"  preferences: {store.prefs}")
        if store.digest:
            print("  settled today: " + " | ".join(store.digest.split("\n")))
    sessions = []
    for path in sorted((paths.research / args.day).glob("*/session.json")):
        try:
            sessions.append(json.loads(path.read_text()))
        except ValueError:
            continue
    research = [s for s in sessions if s.get("purpose") == "research"]
    if sessions:
        spent = sum(float(s.get("spent") or 0) for s in sessions)
        estimated = sum(float(s.get("spentEstimated") or 0) for s in sessions)
        print(f"Researchers: {len(research)} (+{len(sessions) - len(research)} descriptions), "
              f"${spent:.2f} spent" + (f" (+${estimated:.2f} estimated for calls cut off)" if estimated else ""))
        for s in sorted(research, key=lambda s: s.get("n") or 0):
            versions = max([int(o.get("version") or 1) for o in s.get("outputs") or []] or [0])
            title = ((s.get("result") or {}).get("title") or s.get("label") or s.get("brief") or "")[:50]
            thread = (s.get("origin") or {}).get("thread") or "-"
            print(f"  Researcher {s.get('n')}: {s.get('deliverable') or 'research'} {title!r} [{s.get('status')}]"
                  f" in {thread}" + (f", {versions} versions" if versions > 1 else "")
                  + (f", {len(s.get('history') or [])} follow-ups" if s.get("history") else ""))
    frame_timing(paths.interaction_log, args.day)
    print()
    subprocess.run([sys.executable, "-m", "bench.voice_report", "--day", args.day], check=False)


def frame_timing(log, day: str) -> None:
    """Camera-to-screen time per stage, from the studio's reports ("frame-timing", W0-6): the
    median across reports of each stage's p50 and p90."""
    stages: dict[str, dict[str, list[float]]] = {}
    frames = 0
    if log.exists():
        for line in log.open():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") != "frame-timing" or not str(r.get("at", "")).startswith(day):
                continue
            frames += int(r.get("frames") or 0)
            for stage, q in (r.get("stages") or {}).items():
                for name in ("p50", "p90"):
                    if isinstance(q.get(name), (int, float)):
                        stages.setdefault(stage, {}).setdefault(name, []).append(q[name])
    if not stages:
        return
    mid = lambda xs: sorted(xs)[len(xs) // 2] if xs else None  # noqa: E731
    order = ["analyse", "publish", "deliver", "total"]
    parts = [f"{s} p50 {mid(stages[s].get('p50', []))} / p90 {mid(stages[s].get('p90', []))} ms" for s in order if s in stages]
    print(f"Camera to screen ({frames} frames painted): " + "; ".join(parts))


if __name__ == "__main__":
    main()
