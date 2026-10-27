"""The claim checker against a model judge (the researcher repair, Phase 8; docs/bench/claims.md).

A researcher's sourced points are checked against their pages before a result is announced
(research_worker/claims.py: the point's key words and every number on the page). Nothing measured it.
This builds cases whose truth is known by construction from real pages the live researchers cited
(~/MicroscopeMind/assets/research/*/*/session.json), so no label is anyone's opinion:

  verbatim    a sentence taken from the page                              supported
  paraphrase  that sentence reworded by gpt-6.1-sol (not the judge)        supported
  negated     the sentence with its verb negated                          not supported
  number      the sentence with one number changed by a third or more     not supported
  elsewhere   a sentence from a different page, checked against this one  not supported

and runs each checker on every case: "words" (claims.look_up, today's default) and "model" (the fast
model told the page's passages that share the most words with the point; option CLAIM_CHECK "model").
The rule (frozen in docs/bench/research.md): an option replaces "words" only if it is no worse on false
"found" AND no worse on false "not found", and better on one.

  cd services && .venv/bin/python -m bench.claims_bench [--pages 30] [--checkers words,model]

Writes bench/results/claims/claims-<time>.json. Pages are fetched live (safe_fetch.fetch_text); spend is
the paraphrases and the judge's calls, well under $1.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import random
import re
import time
from pathlib import Path

from orchestrator.keys import load_keys
from research_worker import claims
from research_worker.safe_fetch import FetchRefused, fetch_text

OUT = Path(__file__).resolve().parent / "results" / "claims"
ASSETS = Path.home() / "MicroscopeMind" / "assets" / "research"
NEGATE = [(r"\bis\b", "is not"), (r"\bare\b", "are not"), (r"\bcan\b", "cannot"), (r"\bhas\b", "does not have"),
          (r"\bhave\b", "do not have"), (r"\bwill\b", "will not")]


def cited_pages(limit: int, seed: int) -> list[str]:
    pages = set()
    for path in glob.glob(str(ASSETS / "20*" / "r*" / "session.json")):
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            continue
        for claim in (data.get("result") or {}).get("claims") or []:
            url = str(claim.get("source") or "").split("#", 1)[0]
            if url.startswith("http") and not url.endswith(".pdf"):
                pages.add(url)
    pages = sorted(pages)
    random.Random(seed).shuffle(pages)
    return pages[: limit * 2]  # some will not load


def sentences(text: str) -> list[str]:
    """Plain sentences of 12-40 words with at least two content words (no menus, captions or code)."""
    out = []
    for raw in re.split(r"(?<=[.!?])\s+", " ".join(text.split())):
        words = raw.split()
        if 12 <= len(words) <= 40 and raw[0].isupper() and raw.endswith(".") and not re.search(r"[{}<>|=]", raw):
            out.append(raw)
    return out


def negated(sentence: str) -> str | None:
    for pattern, replacement in NEGATE:
        if re.search(pattern, sentence):
            return re.sub(pattern, replacement, sentence, count=1)
    return None


def renumbered(sentence: str) -> str | None:
    match = re.search(r"\b(\d+(?:\.\d+)?)\b", sentence)
    if match is None:
        return None
    value = float(match.group(1))
    changed = value * 1.37 + 3
    text = f"{changed:.0f}" if "." not in match.group(1) else f"{changed:.1f}"
    return sentence[: match.start()] + text + sentence[match.end():]


async def paraphrase(sentence: str) -> str | None:
    import openai

    try:
        response = await openai.AsyncOpenAI().responses.create(
            model="gpt-6.1-sol", reasoning={"effort": "low"},
            input="Reword this sentence so it says exactly the same thing with different words and word order. "
                  "Keep every number and unit. Reply with the sentence only.\n\n" + sentence)
    except Exception:  # noqa: BLE001 - a case that could not be made is left out, said in the counts
        return None
    return " ".join((response.output_text or "").split()) or None


async def judge(point: str, page: str) -> bool | None:
    """Option CLAIM_CHECK "model", as research_worker/claims.check runs it: word for word is found; otherwise the
    check model (gemini-3.5-flash, as lesson_check.CHECK_MODEL) reads the page's best passages. None: could not tell."""
    from google import genai
    from google.genai import types

    indexed = claims.Page(page)
    if claims.verbatim(point, indexed):
        return True
    try:
        response = await genai.Client().aio.models.generate_content(
            model="gemini-3.5-flash", contents=claims.judge_prompt(point, indexed),
            config=types.GenerateContentConfig(response_mime_type="application/json",
                                               thinking_config=types.ThinkingConfig(thinking_level="low")))
        return claims.judged(json.loads(response.text or "{}"))
    except Exception:  # noqa: BLE001 - an unreadable reply is "could not tell", counted apart
        return None


async def build(pages: list[str], per_page: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    texts: dict[str, str] = {}
    for url in pages:
        try:
            texts[url] = await fetch_text(url)
        except (FetchRefused, Exception):  # noqa: BLE001 - a page that does not load is left out
            continue
    usable = {u: t for u, t in texts.items() if len(sentences(t)) >= per_page}
    cases = []
    urls = list(usable)
    for url in urls:
        chosen = rng.sample(sentences(usable[url]), per_page)
        other = rng.choice([u for u in urls if u != url]) if len(urls) > 1 else None
        for sentence in chosen:
            cases.append({"page": url, "kind": "verbatim", "point": sentence, "truth": True})
            reworded = await paraphrase(sentence)
            if reworded:
                cases.append({"page": url, "kind": "paraphrase", "point": reworded, "truth": True})
            for kind, made in (("negated", negated(sentence)), ("number", renumbered(sentence))):
                if made:
                    cases.append({"page": url, "kind": kind, "point": made, "truth": False})
        if other:
            cases.append({"page": url, "kind": "elsewhere", "point": rng.choice(sentences(usable[other])), "truth": False})
    return cases, usable


async def run(args) -> dict:
    pages = cited_pages(args.pages, args.seed)
    cases, texts = await build(pages, args.per_page, args.seed)
    checkers = args.checkers.split(",")
    for case in cases:
        page = texts[case["page"]]
        if "words" in checkers:
            case["words"] = claims.look_up(case["point"], claims.Page(page)).get("found")
        if "model" in checkers:
            case["model"] = await judge(case["point"], page)
    table = {}
    for checker in checkers:
        rows = [c for c in cases if checker in c]
        false_found = sum(c[checker] is True and not c["truth"] for c in rows)
        false_missing = sum(c[checker] is False and c["truth"] for c in rows)
        unclear = sum(c[checker] is None for c in rows)
        table[checker] = {"cases": len(rows), "false_found": false_found, "false_not_found": false_missing,
                          "could_not_tell": unclear,
                          "by_kind": {k: {"right": sum(c[checker] is c["truth"] for c in rows if c["kind"] == k),
                                          "of": sum(c["kind"] == k for c in rows)}
                                      for k in ("verbatim", "paraphrase", "negated", "number", "elsewhere")}}
    return {"pages": len(texts), "cases": cases, "table": table}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=30)
    parser.add_argument("--per-page", type=int, default=2)
    parser.add_argument("--checkers", default="words,model")
    parser.add_argument("--seed", type=int, default=20261010)
    args = parser.parse_args()
    load_keys()
    started = time.time()
    result = asyncio.run(run(args))
    result["seconds"] = round(time.time() - started, 1)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"claims-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(result, indent=2))
    for checker, row in result["table"].items():
        print(f"{checker}: {row['cases']} cases, false found {row['false_found']}, false not found "
              f"{row['false_not_found']}, could not tell {row['could_not_tell']}")
        for kind, n in row["by_kind"].items():
            print(f"   {kind:10} {n['right']}/{n['of']}")
    print(f"pages {result['pages']}; written {path}")


if __name__ == "__main__":
    main()
