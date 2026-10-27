"""Reference photos: where the time goes, and what would bring a verified photo in 2-4 s (W2, 2026-10-11).

Live 2026-10-10/11, 50 requests: 12.2 s to shown at p50 (17.8 s p90), 5 found nothing after 12-20 s. The stages
(execution events): the grounded web search 6.3 s p50, the download 0.6 s, the pixel check 3.8 s.

Arms, each on the same subjects (the ones asked for live, with their words):
  web         today's path: orchestrator.reference.retrieve (grounded search, download, pixel check, one recovery)
  commons     Wikimedia Commons search for the subject (a catalogue, ~0.5 s), the same download and pixel check
  race        both at once; the first photo the pixel check accepts wins (what a "race" option would do)
  check-lite  the pixel check alone with a faster model on every photo the others downloaded, against the
              default check's decision (agreement), and how long it takes

A photo any arm returns is judged by a model of another family (GPT-6.1 Sol): is it a real photograph of what was
asked? So a fast arm that returns the wrong thing does not win on time.

  cd services && .venv/bin/python -m bench.reference_bench [--subjects 20]

Writes results/reference/rows.jsonl and summary.json. Cloud calls only (about $1-2).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "reference"
LOGS = Path.home() / "MicroscopeMind/profiles/companion-20261010/logs/interactions.jsonl"
EXTRA = ["onion epidermis cells", "human sperm cells", "paramecium", "hydra", "diatom", "red blood cells blood smear",
         "celery cross section", "xylem vessels"]  # the demo's other specimens, asked of nobody yet


def asked_subjects(limit: int) -> list[dict]:
    """The subjects people asked for live (deduplicated by their words), plus the demo's other specimens."""
    seen, out = set(), []
    for line in LOGS.open(errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        for a in d.get("actions") or []:
            args = a.get("args") or {}
            if a.get("tool") == "reference_photo" and args.get("subject"):
                key = " ".join(str(args["subject"]).lower().split())
                if key not in seen:
                    seen.add(key)
                    out.append({"subject": args["subject"], "search_terms": args.get("search_terms")})
    out = out[: max(0, limit - len(EXTRA))]
    return out + [{"subject": s, "search_terms": None} for s in EXTRA if s not in seen][: limit - len(out)]


async def commons(subject: str, query: str):
    """Catalogue candidates for the subject, downloaded and checked at once: the first the pixel check accepts."""
    from research_worker import safe_fetch
    from research_worker.retrieval import matches_photo

    words = subject if any(w in subject.lower() for w in ("microscop", "micrograph")) else subject + " microscope"
    pages = await safe_fetch.commons_search(words, limit=3)

    async def one(url):
        image = await safe_fetch.fetch_image(url)
        return image if await matches_photo(query, image.jpeg) else None

    for done in asyncio.as_completed([asyncio.create_task(one(u)) for u in pages]):
        try:
            if (image := await done) is not None:
                return image
        except Exception:
            continue
    return None


async def judged(judge, subject: str, jpeg: bytes) -> bool | None:
    reply = await judge.ask([jpeg], f'Is this image a real photograph of: "{subject}"? A diagram, chart, drawing or a '
                                    'photo of something else is not. Reply JSON {"real_photo_of_it": true|false}.',
                            max_output_tokens=200)
    value = (reply.data or {}).get("real_photo_of_it")
    return value if isinstance(value, bool) else None


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subjects", type=int, default=20)
    parser.add_argument("--judge", default="gpt-6.1-sol")
    parser.add_argument("--lite", default="gemini-3.5-flash-lite", help="the faster pixel-check model to compare")
    args = parser.parse_args()
    from orchestrator.keys import load_keys
    from orchestrator.options import VISION
    from orchestrator.reference import NotFound, retrieve
    from orchestrator.vision import make_vision
    from research_worker import retrieval

    load_keys()
    judge = make_vision(VISION[args.judge].spec)
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in asked_subjects(args.subjects):
        subject, terms = item["subject"], item["search_terms"]
        query = subject
        row = {"subject": subject}
        # web: today's path
        t0 = time.monotonic()
        try:
            found = await retrieve(subject, search_terms=terms)
            row["web"] = {"s": round(time.monotonic() - t0, 2), "page": found.page,
                          "judged": await judged(judge, subject, found.jpeg)}
            web_jpeg = found.jpeg
        except NotFound as exc:
            row["web"] = {"s": round(time.monotonic() - t0, 2), "none": str(exc)[:160]}
            web_jpeg = None
        # commons alone
        t0 = time.monotonic()
        try:
            image = await asyncio.wait_for(commons(subject, query), 45)
        except Exception as exc:  # a failed catalogue search is a miss, said
            image, why = None, f"{type(exc).__name__}: {exc}"[:160]
        else:
            why = "no candidate passed the pixel check"
        row["commons"] = ({"s": round(time.monotonic() - t0, 2), "page": image.page, "judged": await judged(judge, subject, image.jpeg)}
                          if image is not None else {"s": round(time.monotonic() - t0, 2), "none": why})
        # the race, from the two measured on their own (both started together, the first verified photo wins)
        times = [(row[a]["s"], a) for a in ("web", "commons") if "page" in row[a]]
        row["race"] = ({"s": min(times)[0], "from": min(times)[1], "page": row[min(times)[1]]["page"],
                        "judged": row[min(times)[1]]["judged"]} if times
                       else {"s": max(row["web"]["s"], row["commons"]["s"]), "none": "neither found one"})
        # the faster check, on what was downloaded
        lite = []
        for jpeg, arm in ((web_jpeg, "web"), (getattr(image, "jpeg", None), "commons")):
            if jpeg is None:
                continue
            saved = retrieval.LOOKUP_MODEL
            retrieval.LOOKUP_MODEL = args.lite
            t1 = time.monotonic()
            try:
                verdict = await retrieval.matches_photo(query, jpeg)
            except Exception as exc:
                verdict = f"failed: {type(exc).__name__}"
            finally:
                retrieval.LOOKUP_MODEL = saved
            lite.append({"arm": arm, "accepts": verdict, "s": round(time.monotonic() - t1, 2)})
        row["checkLite"] = lite
        rows.append(row)
        print(json.dumps(row)[:300], flush=True)
    with open(OUT / "rows.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    def stat(arm):
        got = [r[arm] for r in rows]
        found = [g for g in got if "page" in g]
        ss = sorted(g["s"] for g in found)
        return {"found": f"{len(found)}/{len(got)}", "judgedRight": f"{sum(1 for g in found if g.get('judged'))}/{len(found)}",
                "p50S": ss[len(ss) // 2] if ss else None, "p90S": ss[int(len(ss) * 0.9)] if ss else None}

    lites = [c for r in rows for c in r["checkLite"]]
    summary = {arm: stat(arm) for arm in ("web", "commons", "race")}
    summary["checkLite"] = {"model": args.lite, "acceptedOfAccepted": f"{sum(1 for c in lites if c['accepts'] is True)}/{len(lites)}",
                            "p50S": sorted(c["s"] for c in lites)[len(lites) // 2] if lites else None}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
