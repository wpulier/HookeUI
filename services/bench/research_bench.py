"""The researcher pacing bench (the researcher repair, Phase 8; rubric frozen in docs/bench/research.md).

Does the code's pacing rule ("forced": finish after STALL_ROUNDS rounds with nothing new, or at 75% of
the time) beat telling the model the facts ("told": time left, rounds left, rounds since it last found
something, each round; made to finish only when under one finish round is left)? Each of 12 briefs runs
at quick effort, deliverable research, with both pacings on both engines, three times; every attempt is
kept. There is no app behind it: the app's own tools answer "the app is not here".

  cd services && .venv/bin/python -m bench.research_bench [--repeats 3] [--engines gpt-6.1-sol,gemini-3.8-flash]
                                                         [--briefs 1-12] [--budget 25]

Then a blind pairwise judgement per brief, engine and repeat (gemini-3.5-flash; the two results unnamed and
in random order). Stops before the total spend passes --budget dollars. Writes
bench/results/research/bench-<time>.json and prints the table the rubric reads.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import tempfile
import time
from pathlib import Path

from orchestrator.keys import load_keys
from research_worker.engines import make_engine
from research_worker.researcher import Researcher

OUT = Path(__file__).resolve().parent / "results" / "research"
BRIEFS = [
    "Explain the cellular mechanism of how guard cells open and close a stoma, including ion transport, turgor "
    "pressure and microfibril orientation.",
    "Find reference images of neutrophil lobulated nuclei and explain why the lobes appear connected by thin strands, "
    "or separated, in a 2D microscope view.",
    "Anatomy and function of the insect leg segment joint in Musca domestica, including articulation and cuticle structure.",
    "How do the pith, the vascular bundle (xylem and phloem) and the cortex work together in a plant stem?",
    "Why do different sperm cells from the same father carry unique chromosome combinations?",
    "What distinguishes normal from abnormal spermatozoa morphology in a semen wet mount, by the WHO criteria?",
    "How does an unstained, fresh plant stem cross-section look under a light microscope compared with a stained one?",
    "What is a platelet, what is its structure, and what is its role in clotting, at the level a student understands?",
    "What features tell sclerenchyma fibres from xylem vessels in a stem cross-section?",
    "Why are red blood cells biconcave, and what of that shape can be seen at 400x and at 1000x?",
    "What typical swimming speeds do human sperm have, and what limits a 4-second track on a wet mount from measuring them?",
    "What would confirm or rule out that small round dark spots on a blood smear are Howell-Jolly bodies rather than "
    "dust on the optics?",
]
WANTS_PICTURES = {1}  # brief 2 (index 1) asks for reference images: a result without any lacks a requested part


async def no_app(op: str, **kw) -> dict:
    return {"ok": False, "error": "the app is not here (a bench run): work from the web"}


async def one(brief: str, engine: str, pacing: str, folder: Path) -> dict:
    events: list[dict] = []
    spec = {"rid": "bench", "dir": str(folder), "brief": brief, "effort": "quick", "deliverable": "research",
            "engine": engine, "pacing": pacing, "purpose": "research"}
    r = Researcher(spec, events.append, no_app)
    started = time.monotonic()
    await r.run(make_engine(engine))
    done = next((e for e in reversed(events) if e.get("event") == "done"), {})
    steps = [e for e in events if e.get("event") == "step"]
    result = done.get("result") or {}
    claims = [c for c in result.get("claims") or [] if isinstance(c, dict)]
    checked = [c for c in claims if c.get("found") is not None]
    return {
        "status": done.get("status"), "seconds": round(time.monotonic() - started, 1),
        "spent": float(done.get("spent") or 0) + float(done.get("spent_estimated") or 0),
        "work_lost": result == {} and (bool(r.recorded) or bool(r.search_sources)),
        "missing_parts": (not result.get("refs")) if brief in [BRIEFS[i] for i in WANTS_PICTURES] else False,
        "claims_found": sum(c["found"] is True for c in checked), "claims_checked": len(checked),
        "sources": len(result.get("sources") or []), "rounds": r.round_n,
        "stalls": sum(s.get("kind") == "stall" for s in steps),
        "wrap_ups": sum("Time is nearly up" in str(s.get("text")) for s in steps),
        "result": result,
    }


async def judge(brief: str, a: dict, b: dict) -> str:
    """Which of two results answers the brief better, unnamed: "A", "B" or "tie" (or "error")."""
    from google import genai
    from google.genai import types

    def text(r: dict) -> str:
        res = r.get("result") or {}
        return json.dumps({k: res.get(k) for k in ("summary", "details", "report", "tables", "limitations", "sources")},
                          ensure_ascii=False)[:20000]

    prompt = ("Two research results answer the same brief. Judge which answers it better: specific, correct, sourced, "
              "and honest about what it could not find. Ignore length for its own sake. Answer JSON "
              '{"better": "A" | "B" | "tie"}.\n\nBRIEF:\n' + brief + "\n\nRESULT A (data):\n" + text(a)
              + "\n\nRESULT B (data):\n" + text(b))
    try:
        response = await genai.Client().aio.models.generate_content(
            model="gemini-3.5-flash", contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json",
                                               thinking_config=types.ThinkingConfig(thinking_level="low")))
        said = json.loads(response.text or "{}").get("better")
    except Exception:  # noqa: BLE001 - counted apart, never a win
        return "error"
    return said if said in ("A", "B", "tie") else "error"


def table(runs: list[dict], verdicts: list[dict], engines: list[str]) -> dict:
    out = {}
    for engine in engines:
        row = {}
        for pacing in ("forced", "told"):
            mine = [r for r in runs if r["engine"] == engine and r["pacing"] == pacing]
            sets = [[r for r in mine if r["repeat"] == k] for k in sorted({r["repeat"] for r in mine})]
            per_set = lambda f: [f(s) for s in sets if s]  # noqa: E731
            row[pacing] = {
                "runs": len(mine),
                "work_lost": sum(r["work_lost"] for r in mine),
                "outcome": {s: sum(r["status"] == s for r in mine) for s in ("done", "partial", "failed", "timeout", "over_budget")},
                "missing_parts": sum(r["missing_parts"] for r in mine),
                "claims_found_share": round(sum(r["claims_found"] for r in mine) / max(1, sum(r["claims_checked"] for r in mine)), 3),
                "per_set": {"done": per_set(lambda s: sum(r["status"] == "done" for r in s)),
                            "claims_found_share": per_set(lambda s: round(sum(r["claims_found"] for r in s)
                                                                          / max(1, sum(r["claims_checked"] for r in s)), 3))},
                "seconds_median": sorted(r["seconds"] for r in mine)[len(mine) // 2] if mine else None,
                "spent": round(sum(r["spent"] for r in mine), 4),
                "stalls": sum(r["stalls"] for r in mine), "wrap_ups": sum(r["wrap_ups"] for r in mine),
            }
        mine = [v for v in verdicts if v["engine"] == engine]
        row["pairs"] = {"told_wins": sum(v["winner"] == "told" for v in mine),
                        "forced_wins": sum(v["winner"] == "forced" for v in mine),
                        "ties": sum(v["winner"] == "tie" for v in mine), "errors": sum(v["winner"] == "error" for v in mine)}
        out[engine] = row
    return out


async def bench(args) -> dict:
    """Every run of the matrix, `--parallel` at a time, stopping new ones before the spend passes --budget; then the
    blind pairwise judgement of each complete pair. Every attempt is kept, failures included."""
    engines = args.engines.split(",")
    first, last = (int(x) for x in args.briefs.split("-"))
    briefs = list(enumerate(BRIEFS))[first - 1:last]
    plan = [(repeat, n, brief, engine, pacing) for repeat in range(args.repeats) for n, brief in briefs
            for engine in engines for pacing in ("forced", "told")]
    runs, spent, skipped = [], 0.0, []
    gate = asyncio.Semaphore(args.parallel)
    with tempfile.TemporaryDirectory() as tmp:
        async def go(repeat, n, brief, engine, pacing):
            nonlocal spent
            async with gate:
                if spent >= args.budget:
                    skipped.append((repeat, n + 1, engine, pacing))
                    return
                row = await one(brief, engine, pacing, Path(tmp) / f"{repeat}-{n}-{engine}-{pacing}")
                spent += row["spent"]
                runs.append({"brief": n + 1, "engine": engine, "pacing": pacing, "repeat": repeat, **row})
                print(f"r{repeat + 1} brief {n + 1:2} {engine:16} {pacing:6} {row['status']:9} {row['seconds']:5.1f}s "
                      f"${row['spent']:.3f}  total ${spent:.2f}", flush=True)

        await asyncio.gather(*(go(*p) for p in plan))
    verdicts = []
    for repeat in range(args.repeats):
        for n, brief in briefs:
            for engine in engines:
                pair = {r["pacing"]: r for r in runs if (r["repeat"], r["brief"], r["engine"]) == (repeat, n + 1, engine)}
                if len(pair) < 2:
                    continue
                order = random.Random(f"{repeat}-{n}-{engine}").sample(["forced", "told"], 2)
                said = await judge(brief, pair[order[0]], pair[order[1]])
                verdicts.append({"brief": n + 1, "engine": engine, "repeat": repeat,
                                 "winner": {"A": order[0], "B": order[1]}.get(said, said)})
    for r in runs:
        r.pop("result", None)
    return {"runs": runs, "verdicts": verdicts, "spent": round(spent, 4),
            "stopped": f"{len(skipped)} runs not started: the ${args.budget} budget was reached" if skipped else None,
            "table": table(runs, verdicts, engines)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--engines", default="gpt-6.1-sol,gemini-3.8-flash")
    parser.add_argument("--briefs", default="1-12")
    parser.add_argument("--budget", type=float, default=25.0)
    parser.add_argument("--parallel", type=int, default=4)
    args = parser.parse_args()
    load_keys()
    result = asyncio.run(bench(args))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"bench-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result["table"], indent=2))
    print(f"spent ${result['spent']:.2f}" + (f"; {result['stopped']}" if result["stopped"] else "") + f"; written {path}")


if __name__ == "__main__":
    main()
