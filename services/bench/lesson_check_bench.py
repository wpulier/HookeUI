"""Do lessons' quiz steps need the picture, and does "rewrite" make them? (W6-3;
research_worker/lesson_check.py, option LESSON_CHECK)

For a handful of real lessons (each researcher folder under --research that made a lesson whose
steps have answers, read from its content or its HTML, with the photo it started from), plus one
fixed lesson on that first photo:
  before     the check: a model that sees no picture answers every step; the same model grades
             each answer against the lesson's own (the steps it got right do not need the picture)
  rewrite    those steps go once to the lesson writer with the photo (as in a live run)
  after      the rewritten steps are checked again the same way
It reports, per lesson and in total: steps, answered right without the picture before, rewritten,
still answered right after, spend and seconds; and writes every rewritten step for reading.

  cd services && .venv/bin/python -m bench.lesson_check_bench --research ../../microscope-mind/services/research

Cloud calls (Gemini 3.8 Flash for the check, gpt-6.1-sol for the rewrite): a few cents a lesson.
Reads the research folders only; writes results/lesson-check-<mode>.json (--mode check: the
check alone, as option "check" runs it).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path

from orchestrator.keys import load_keys
from orchestrator.research.content import lesson_from_html
from research_worker import researcher as rw
from research_worker.lesson_check import blind_prompt, grade_prompt, right_ids
from research_worker.results import lesson_content

HERE = Path(__file__).resolve().parent
FIXED = lesson_content("Guard cells", ["Two guard cells surround each pore", "They swell with water to open it"],
                       [{"question": "What surrounds the pore?", "answer": "Two guard cells", "hint": "count them"},
                        {"question": "What opens the pore?", "answer": "The guard cells swelling with water"},
                        {"question": "Where are stomata most common?", "answer": "On the underside of leaves"}])


def lessons(root: Path, limit: int) -> list[tuple[str, dict, Path]]:
    """(name, lesson content, photo) for each saved lesson whose steps have answers."""
    found = []
    for session in sorted(root.glob("**/session.json")):
        try:
            data = json.loads(session.read_text())
        except (OSError, ValueError):
            continue
        photo = next((p for p in (session.parent / "inputs/crop.jpg", session.parent / "inputs/frame.jpg") if p.exists()), None)
        for output in data.get("outputs") or []:
            if output.get("kind") != "html" or photo is None:
                continue
            content = output.get("content") or lesson_from_html((session.parent / output["path"]).read_text(errors="ignore")) or {}
            if content.get("steps") and all(s.get("answer") for s in content["steps"]):
                found.append((f"{session.parent.parent.name}/{data.get('rid')}", content, photo))
    if found:
        found.append(("fixed/guard-cells", FIXED, found[0][2]))
    return found[:limit]


async def blind_right(r, content: dict, ids: list[str] | None = None) -> list[str]:
    """The steps (all, or `ids`) a model with no picture answers right."""
    steps = [s for s in content["steps"] if ids is None or s["id"] in ids]
    part = {**content, "steps": steps}
    answers = (await rw.fast_json(blind_prompt(part), r)).get("answers")
    return right_ids(part, await rw.fast_json(grade_prompt(part, answers if isinstance(answers, dict) else {}), r))


async def one(name: str, content: dict, photo: Path, work: Path, mode: str) -> dict:
    directory = work / name.replace("/", "-")
    (directory / "inputs").mkdir(parents=True)
    shutil.copy(photo, directory / "inputs" / "crop.jpg")
    r = rw.Researcher({"rid": "bench", "dir": str(directory), "effort": "standard", "brief": "a lesson",
                       "lesson_check": mode}, lambda e: None, None)
    started = time.monotonic()
    edited, record = await r._check_lesson(content)
    took = time.monotonic() - started
    rewritten = (record or {}).get("rewritten") or []
    after = await blind_right(r, edited, rewritten) if rewritten else []
    return {"lesson": name, "steps": len(content["steps"]), "before": (record or {}).get("answered_without_picture"),
            "rewritten": rewritten, "still_answerable_after": after, "check_and_rewrite_s": round(took, 1),
            "check_s": (record or {}).get("check_s"),
            "usd": round(r.budget.spent, 4), "error": None if record else "the check did not run (see steps)",
            "changes": [{"id": i, "was": next(s for s in content["steps"] if s["id"] == i),
                         "now": next(s for s in edited["steps"] if s["id"] == i)} for i in rewritten],
            "log": [json.loads(line)["text"] for line in (directory / "steps.jsonl").read_text().splitlines()]
            if (directory / "steps.jsonl").exists() else []}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research", type=Path, default=Path("research"))
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--mode", choices=("rewrite", "check"), default="rewrite")
    args = parser.parse_args()
    load_keys()
    found = lessons(args.research, args.limit)
    if not found:
        raise SystemExit(f"no lessons with answered steps under {args.research}")
    work = Path(tempfile.mkdtemp(prefix="lesson-check-"))
    rows = [await one(name, content, photo, work, args.mode) for name, content, photo in found]
    total = {k: sum(len(r[k] or []) if isinstance(r[k], list) else r[k] for r in rows)
             for k in ("steps", "before", "rewritten", "still_answerable_after")}
    total.update(usd=round(sum(r["usd"] for r in rows), 4), lessons=len(rows),
                 seconds_p50=sorted(r["check_and_rewrite_s"] for r in rows)[len(rows) // 2],
                 check_s_p50=sorted(r["check_s"] or 0 for r in rows)[len(rows) // 2], mode=args.mode)
    for r in rows:
        print(f"{r['lesson']:<28} steps {r['steps']}  right without picture {len(r['before'] or [])}  rewritten "
              f"{len(r['rewritten'])}  still right after {len(r['still_answerable_after'])}  {r['check_and_rewrite_s']} s  ${r['usd']}")
        for c in r["changes"]:
            print(f"    {c['id']}: {c['was']['prompt'][:90]}\n     -> {c['now']['prompt'][:140]} [{c['now']['answer'][:80]}]")
    print("total", total)
    out = HERE / "results" / f"lesson-check-{args.mode}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"total": total, "rows": rows}, indent=1, ensure_ascii=False))
    print("wrote", out)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
