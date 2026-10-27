"""Phase 0 probe: does a researcher's "continue" work against the real APIs?

A researcher's run ends on a `finish` function call. On gpt-6.1-sol the manager then continues the
run with `previous_response_id` set to that last response, sending only a user message: the
Responses API is expected to refuse a chain whose function call has no output. This runs the real
worker loop (Researcher + engine) twice per engine, a short start and then a continue the way
`ResearchManager._continue` builds it, and records exactly what each call returned.

  cd services && .venv/bin/python -m bench.research_probe [--engines gpt-6.1-sol,gemini-3.8-flash] [--continue-as chain|package]

`--continue-as chain` (the code as it was): Sol continues the response chain, Gemini gets the
800 + 600 character brief. `package` (after Phase 3): every engine gets the same explicit context
and no chain. Writes bench/results/research/probe-<time>.json. Costs well under $1 (instant effort).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from pathlib import Path

from orchestrator.keys import load_keys
from research_worker.engines import make_engine
from research_worker.researcher import Researcher

OUT = Path(__file__).resolve().parent / "results" / "research"
BRIEF = ("What do guard cells do in a leaf epidermis seen under a light microscope at about 100x? "
         "Answer in two sentences.")
EFFORT = "instant"
NEXT = "Keep going: how could the user tell an open stoma from a closed one on this slide?"


async def one_run(engine_name: str, spec: dict) -> dict:
    events: list[dict] = []
    r = Researcher(spec, events.append, None)
    started = time.monotonic()
    await r.run(make_engine(engine_name))
    done = next((e for e in reversed(events) if e.get("event") == "done"), {})
    steps = [e.get("text") for e in events if e.get("event") == "step"]
    return {"status": done.get("status"), "seconds": round(time.monotonic() - started, 1),
            "spent": done.get("spent"), "response_id": done.get("response_id"),
            "summary": ((done.get("result") or {}).get("summary") or "")[:400],
            "failures": [s for s in steps if s and (s.startswith("Failed") or s.startswith("(at "))],
            "steps": steps[-12:],
            "brief_chars": len(spec["brief"])}


async def probe(engine_name: str, continue_as: str, root: Path) -> dict:
    base = {"rid": f"probe-{engine_name}", "effort": EFFORT, "deliverable": "research", "engine": engine_name}
    first = await one_run(engine_name, {**base, "dir": str(root / engine_name / "1"), "brief": BRIEF})
    if continue_as == "chain":
        # As ResearchManager._continue did: the chain on Sol, else 800 + 600 characters.
        if first["response_id"]:
            spec = {"brief": NEXT[:1000], "previous_response_id": first["response_id"]}
        else:
            spec = {"brief": f"{NEXT[:800]}\n\n(Your earlier answer: {first['summary'][:600]})"}
    else:
        spec = {"brief": (f"Original request: {BRIEF}\n\nYour finding so far: {first['summary']}\n\n"
                          f"Now: {NEXT}")}
    second = await one_run(engine_name, {**base, "dir": str(root / engine_name / "2"), **spec})
    return {"engine": engine_name, "continue_as": continue_as, "start": first, "continue": second,
            "continue_worked": second["status"] == "done" and not second["failures"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engines", default="gpt-6.1-sol,gemini-3.8-flash")
    parser.add_argument("--continue-as", choices=("chain", "package"), default="chain")
    parser.add_argument("--effort", default="instant")
    args = parser.parse_args()
    global EFFORT
    EFFORT = args.effort
    keys = load_keys()
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for name in args.engines.split(","):
            need = "OPENAI_API_KEY" if not name.startswith("gemini") else "GEMINI_API_KEY"
            if not keys.get(need):
                results.append({"engine": name, "skipped": f"{need} is not set"})
                continue
            results.append(asyncio.run(probe(name, args.continue_as, Path(tmp))))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"probe-{args.continue_as}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(results, indent=2))
    for row in results:
        if "skipped" in row:
            print(f"{row['engine']}: skipped ({row['skipped']})")
            continue
        print(f"{row['engine']} [{row['continue_as']}]: start {row['start']['status']} ${row['start']['spent']}, "
              f"continue {row['continue']['status']} ${row['continue']['spent']}")
        for failure in row["continue"]["failures"]:
            print("   ", failure)
    print(f"written {path}")


if __name__ == "__main__":
    main()
