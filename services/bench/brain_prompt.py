"""Does adding the brain's tools section to the answer prompt cost accuracy or speed?

Runs the 15 labelled questions (questions.json) through the chat answer prompt as it was
(ASK) and as the brain sends it (ASK + tools section + scene table), and scores whether a
returned target lands on a labelled target, like bench/grounding.py.

  python -m bench.brain_prompt [--model gemini-3.8-flash] [--repeats 2]
  python -m bench.brain_prompt --followup [--model ...] [--repeats 5]

--followup measures the brain's follow-up call after a query tool (brain/loop.py): the live case of
2026-10-07 (flow 5), "Pull up the cells from earlier", whose follow-up only promised ("Let me find the
cells…") and listed no action. Two arms: the follow-up as it was ("Now give your final answer.") and as it
is (also told that nothing runs after it); scored by whether the answer lists an action (present).
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import numpy as np

from bench.grounding import prepare, score
from orchestrator.brain.toolbox import catalogue
from orchestrator.coords import gemini_box_to_frame
from orchestrator.keys import load_keys
from orchestrator.options import VISION
from orchestrator.prompts import ASK, with_tools
from orchestrator.vision import make_vision

BENCH = Path(__file__).resolve().parent


async def one(vision, prompt: str, item: dict, prepared: dict) -> dict:
    result = await vision.ask([prepared["clean"]], prompt)
    data = result.data or {}
    correct = False
    for target in data.get("targets") or []:
        box = target.get("box_2d") if isinstance(target, dict) else None
        if isinstance(box, list) and len(box) == 4:
            b = gemini_box_to_frame([float(v) for v in box], prepared["crop"])
            if score(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), b, item["targets"], item["tolerance"]):
                correct = True
    return {"correct": correct, "ms": result.latency_ms, "actions": data.get("actions"), "error": result.error}


FOLLOWUP_QUESTION = "Pull up the cells from earlier"
FOLLOWUP_RESULTS = "- resources: e1: Cell outlines · 12:49 (207 instances segmented by cellpose-sam), not shown"
FOLLOWUP_ARMS = {
    "follow-as-it-was": "Now give your final answer.",
    "follow-nothing-after": "Now give your final answer. Nothing runs after it.",
    "follow-list-actions": "Now give your final answer. Nothing runs after it: list now the actions it needs, or say what you found.",
}
# As live: the first call's own answer is in the conversation, and the look saw cells on screen.
FOLLOWUP_HISTORY = "user: Pull up the cells from earlier\nassistant: Let me find the cells we segmented earlier today."


async def followup(vision, tools: str, repeats: int) -> dict:
    """The follow-up call's two wordings on the live case: how often the final answer lists an action."""
    rows = {name: [] for name in FOLLOWUP_ARMS}
    for _ in range(repeats):
        for name, ending in FOLLOWUP_ARMS.items():
            question = f"{FOLLOWUP_QUESTION}\n\n(Results of the tools you used:\n{FOLLOWUP_RESULTS}\n{ending})"
            prompt = with_tools(ASK, tools, "nothing yet").format(context="", history=FOLLOWUP_HISTORY, question=question)
            result = await vision.ask([], prompt)
            actions = [a for a in ((result.data or {}).get("actions") or []) if isinstance(a, dict)]
            rows[name].append({"acted": any(a.get("tool") in ("present", "show_result") for a in actions),
                               "none": not actions,
                               "actions": [a.get("tool") for a in actions], "answer": (result.data or {}).get("answer"),
                               "ms": result.latency_ms, "error": result.error})
    return {name: {"acted": f"{sum(r['acted'] for r in rs)}/{len(rs)}", "noAction": f"{sum(r['none'] for r in rs)}/{len(rs)}",
                   "errors": sum(bool(r["error"]) for r in rs),
                   "rows": rs} for name, rs in rows.items()}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemini-3.8-flash")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--followup", action="store_true", help="measure the follow-up call after a query (flow 5)")
    args = parser.parse_args()
    load_keys()
    vision = make_vision(VISION[args.model].spec)
    tools = catalogue().prompt_section()
    if args.followup:
        summary = await followup(vision, tools, args.repeats)
        print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in summary.items()}, indent=2))
        (BENCH / "results" / f"brain-followup-{args.model}.json").write_text(json.dumps(summary, indent=2))
        return
    questions = json.loads((BENCH / "questions.json").read_text())["questions"]
    prepared = {q["frame"]: prepare(q) for q in questions}
    arms = {
        "ask": lambda q: ASK.format(context="", scene="nothing yet", history="(none)", question=q["question"]),
        "ask+tools": lambda q: with_tools(ASK, tools, "nothing yet").format(context="", history="(none)", question=q["question"]),
    }
    rows = {name: [] for name in arms}
    for _ in range(args.repeats):
        for q in questions:
            for name, build in arms.items():
                rows[name].append(await one(vision, build(q), q, prepared[q["frame"]]))
    summary = {}
    for name, rs in rows.items():
        ms = [r["ms"] for r in rs if not r["error"]]
        summary[name] = {
            "right": f"{sum(r['correct'] for r in rs)}/{len(rs)}",
            "p50Ms": round(float(np.median(ms))) if ms else None,
            "p90Ms": round(float(np.percentile(ms, 90))) if ms else None,
            "errors": sum(bool(r["error"]) for r in rs),
            "actionsUsed": sum(bool(r["actions"]) for r in rs),
        }
    print(json.dumps(summary, indent=2))
    (BENCH / "results" / f"brain-prompt-{args.model}.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
