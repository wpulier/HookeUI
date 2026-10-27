"""Does the pointing reflex point at what a spoken question is about, and how fast? (option REFLEX; ADR 0016)

The fast bench's fixed notes (a running look with two stomata and a dark speck, one stoma outlined and selected):
for each question, the ids it is about (any listed alternative is right); for remarks, fillers and cut-off words,
nothing. Arms: the full fast pass told to only point (REFLEX "after-voice"), and the lean prompt (brain/fast.py
LEAN_POINT) on each model. Right calls and time per arm, both, because the reflex has to be right before the voice
finishes its first sentence.

  cd services && .venv/bin/python -m bench.point_bench [--repeats 2]

Writes results/point-bench.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from bench.fast_bench import SCREEN, notes
from bench.tool_choice import registry
from orchestrator.brain.fast import decide, lean
from orchestrator.keys import load_keys
from orchestrator.options import VISION
from orchestrator.vision import make_vision

BENCH = Path(__file__).resolve().parent
# (words, the ids it is about: a set, or a tuple of sets any of which is right; empty: point at nothing)
CASES = [
    ("What's the little dot?", {"s3"}),
    ("What's that dark speck in the lower left?", {"s3"}),
    ("What are those ovals?", {"s1", "s2"}),
    ("What are those two openings?", {"s1", "s2"}),
    ("What's the one in the middle?", ({"s1"}, {"o1"}, {"s1", "o1"})),
    ("And the one at the top right?", {"s2"}),
    ("What are all those cells around them?", {"s4"}),
    ("Is the speck moving?", {"s3"}),
    ("What does a guard cell do?", set()),
    ("So this is the microscope I was telling you about. Pretty cool, right?", set()),
    ("Okay", set()),
    ("Incredible.", set()),
    ("Can you close the", set()),
    ("I'm going to change the slide now", set()),
]


def ids(decision) -> set[str]:
    return {str(t) for name, args in decision.actions for t in (args or {}).get("things", []) or []} | \
           {str((args or {}).get("object")) for name, args in decision.actions if name == "select"}


def right(got: set[str], expected) -> bool:
    return any(got == option for option in (expected if isinstance(expected, tuple) else (expected,)))


async def arm(name: str, model: str, repeats: int) -> dict:
    vision = make_vision(VISION[model].spec)
    tools = registry().prompt_section()
    rows = []
    for _ in range(repeats):
        for words, expected in CASES:
            moment = notes(SCREEN)
            t0 = time.perf_counter()
            if name == "full":
                d = await decide(moment, words, vision, tools, model, point_only=True)
            else:
                d = await lean(moment, words, vision, model)
            ms = (time.perf_counter() - t0) * 1000
            got = ids(d)
            rows.append({"words": words, "got": sorted(got), "right": right(got, expected), "ms": round(ms)})
    times = sorted(r["ms"] for r in rows)
    pct = lambda p: times[min(len(times) - 1, int(round(p / 100 * (len(times) - 1))))]  # noqa: E731
    return {"arm": f"{name}:{model}", "right": sum(r["right"] for r in rows), "of": len(rows),
            "p50Ms": pct(50), "p90Ms": pct(90), "misses": [r for r in rows if not r["right"]]}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--arms", default="full:gemini-3.8-flash,lean:gemini-3.8-flash,lean:gemini-3.5-flash,"
                                          "lean:gemini-3.5-flash-lite")
    args = parser.parse_args()
    load_keys()
    results = []
    for spec in args.arms.split(","):
        name, model = spec.split(":", 1)
        result = await arm(name, model, args.repeats)
        results.append(result)
        print(f"{result['arm']:32s} {result['right']}/{result['of']}  p50 {result['p50Ms']} ms  p90 {result['p90Ms']} ms")
        for miss in result["misses"][:6]:
            print(f"    miss: {miss['words']!r} -> {miss['got']}")
    (BENCH / "results").mkdir(exist_ok=True)
    (BENCH / "results" / "point-bench.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
