"""How fast the voice answers live: first words after the user stops, hand-offs, sessions.

Reads the interaction log (services/logs/interactions.jsonl unless orchestrator/paths.py says
otherwise; "voice-reply", "voice-delegation", "voice-session-end", written by
orchestrator/voice/route.py) and prints a summary per day.

  make voice-report            (or: cd services && .venv/bin/python -m bench.voice_report [--day 2026-10-05])
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

from orchestrator.paths import Paths


def pct(values: list[float], p: float):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default="", help="YYYY-MM-DD (default: every day in the log)")
    parser.add_argument("--log", default=str(Paths.from_env().interaction_log))
    args = parser.parse_args()
    keys = ("first", "first_delegated", "delegation", "sessions", "hands", "vision_start", "after_words", "server_gap",
            "words_wait", "hands_wait", "lag", "drift")
    days: dict[str, dict] = defaultdict(lambda: {**{k: [] for k in keys}, "replies": 0, "hands_used": 0, "voice_turns": 0})
    kinds = ("voice-reply", "voice-delegation", "voice-session-end", "voice-handoff", "voice-handoff-wait", "hands", "turn")
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    with open(args.log) as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            day = str(r.get("at", ""))[:10]
            if args.day and day != args.day or r.get("kind") not in kinds:
                continue
            if r.get("kind") == "turn" and r.get("channel") != "voice":
                continue
            d = days[day]
            kind = r.get("kind")
            if kind == "hands" and num(r.get("ms")):
                d["hands"].append(r["ms"])
            elif kind == "turn":
                d["voice_turns"] += 1
                d["hands_used"] += bool(r.get("handsUsed"))
                if num(r.get("visionStartMs")):
                    d["vision_start"].append(r["visionStartMs"])
            elif kind == "voice-handoff":
                if num(r.get("afterWordsMs")):
                    d["after_words"].append(r["afterWordsMs"])
                if num(r.get("serverGapMs")):
                    d["server_gap"].append(r["serverGapMs"])
            elif kind == "voice-handoff-wait":
                d["words_wait"].append(r.get("wordsWaitMs") or 0)
                d["hands_wait"].append(r.get("handsWaitMs") or 0)
            elif kind == "voice-session-end":
                if num(r.get("lagP50Ms")):
                    d["lag"].append(r["lagP50Ms"])
                if num(r.get("lagDriftMs")):
                    d["drift"].append(r["lagDriftMs"])
            if r.get("kind") == "voice-reply" and isinstance(r.get("firstWordsMs"), (int, float)):
                d["replies"] += 1
                (d["first_delegated"] if r.get("delegated") else d["first"]).append(r["firstWordsMs"])
            elif r.get("kind") == "voice-delegation" and isinstance(r.get("ms"), (int, float)):
                d["delegation"].append(r["ms"])
            elif r.get("kind") == "voice-session-end":
                d["sessions"].append(r.get("seconds") or 0)
    if not days:
        print("No voice timing in the log yet (it is recorded from the next voice session on).")
        return
    for day, d in sorted(days.items()):
        print(f"{day}: {d['replies']} replies in {len(d['sessions'])} voice sessions "
              f"(median session {pct(d['sessions'], 50)} s)")
        print(f"  first words, answered from notes: p50 {pct(d['first'], 50)} ms, p90 {pct(d['first'], 90)} ms "
              f"(n={len(d['first'])})")
        print(f"  first words, handed to the app:   p50 {pct(d['first_delegated'], 50)} ms, "
              f"p90 {pct(d['first_delegated'], 90)} ms (n={len(d['first_delegated'])})")
        print(f"  hand-off until the app's findings: p50 {pct(d['delegation'], 50)} ms, p90 {pct(d['delegation'], 90)} ms "
              f"(n={len(d['delegation'])})")
        line = lambda name, values: print(f"  {name}: p50 {pct(values, 50)} ms, p90 {pct(values, 90)} ms (n={len(values)})")  # noqa: E731
        if d["hands"]:
            line("hands (fast pass on spoken words)", d["hands"])
        if d["voice_turns"]:
            print(f"  voice turns: {d['voice_turns']}, {d['hands_used']} reused what hands did")
        if d["vision_start"]:
            line("vision call started after the turn began", d["vision_start"])
        if d["after_words"]:
            line("hand-off after the user's last words (our clock)", d["after_words"])
        if d["server_gap"]:
            line("hand-off after the last words (server's clock)", d["server_gap"])
        if d["words_wait"]:
            line("hand-off waiting for its words", d["words_wait"])
            line("hand-off waiting for hands", d["hands_wait"])
        if d["lag"]:
            line("server word times lag our clock by", d["lag"])
            line("and drift within a session by", d["drift"])


if __name__ == "__main__":
    main()
