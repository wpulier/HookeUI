"""The two voices side by side on the same plan, against the standard (docs/bench/voice-journeys.md): GPT-Live and the
one brain, each line pass, FAIL or not measured, and whether the default may switch. It reads the runs' result.json
(run.py); it starts and spends nothing.

  cd services && .venv/bin/python -m bench.voice_journeys.compare /tmp/p25-live /tmp/p25-rt [--out board.md]

Bench-only, not an arena trial kind: the arena (orchestrator/arena.py) collects a person's blind A/B/C votes in the
studio, while this compares measured numbers with fixed thresholds. The default switches to the one brain with hold to
talk only when the one brain meets every line (a line not measured is not met); being better than GPT-Live is not
enough, and GPT-Live's own result is shown for comparison.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ONE_BRAIN = "gpt-realtime-2.1"


def load(path: str) -> dict:
    p = Path(path)
    return json.loads((p / "result.json" if p.is_dir() else p).read_text())


def compare(results: list[dict]) -> str:
    plans = {r["about"].get("plan") for r in results}
    head = [f"{r['about'].get('voice')} ({r['about'].get('talk')}, {r['about'].get('reps')} runs)" for r in results]
    out = ["# Live partner journey: the voices against the standard", "",
           f"Plan {', '.join(sorted(p or '?' for p in plans))}. "
           + "; ".join(f"{h}: {r['about'].get('evidence', '')}, commit {r['about'].get('commit', '?')}, "
                       f"judged by {r['about'].get('judge', 'none')}" for h, r in zip(head, results))
           + ". Recorded audio is not Will in a room.", ""]
    if len({r["about"].get("judge") for r in results}) > 1:
        out += ["**Judged by different judges: the judged lines (false done, outcomes said, partner quality) are not "
                "comparable.**", ""]
    if len(plans) > 1:
        out += ["**Not comparable: the runs used different plans.**", ""]
    out += ["| Area | Line | Standard | " + " | ".join(head) + " |", "|---|---|---|" + "---|" * len(results)]
    boards = [{(l["area"], l["line"]): l for l in r["scoreboard"]} for r in results]
    for key, line in boards[0].items():
        cells = []
        for board in boards:
            got = board.get(key)
            cells.append("—" if got is None else f"{got['value']} **{got['result']}**")
        out.append(f"| {key[0]} | {key[1]} | {line['threshold']} | " + " | ".join(cells) + " |")
    one = [r for r in results if r["about"].get("voice") == ONE_BRAIN and r["about"].get("talk") == "hold"]
    out += ["", "**Switch the default to the one brain with hold to talk: " +
            ("yes** (it meets every line of the standard)." if one and all(r["meets"] for r in one) and len(plans) == 1
             else "no**" + (" (no one-brain hold run here)." if not one else
                            f" ({sum(1 for l in one[0]['scoreboard'] if l['result'] != 'pass')} lines not met)."))]
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", nargs="+", help="run folders (or their result.json), GPT-Live first")
    parser.add_argument("--out", help="also write the board here")
    args = parser.parse_args()
    text = compare([load(p) for p in args.results])
    if args.out:
        Path(args.out).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
