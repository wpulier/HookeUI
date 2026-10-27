"""The naming bench: does the app name things right, does it know when it doesn't, and how much
do the words around a picture steer the name? (`make naming-bench`; roadmap W0-3.)

Every labelled picture (bench/naming/fetch.py) is put into an eyepiece circle the way the app
sees it (composite.py) and named through the app's own paths: naming a click (the IDENTIFY
prompt with the outlined view and close-up, orchestrator/agent.identify_images) and the running
look (the SIGHT prompt). Each picture is asked under four conditions:

  right   told the session's specimen correctly ("a blood smear")
  none    told nothing
  wrong   told the wrong specimen ("a leaf epidermis")
  grey    a blank grey disc, told the right specimen: anything named comes from the words

Scored per vision option by score.py: fine and coarse accuracy, abstention, calibration (ECE),
per-class accuracy and confusions, context leak and consistency across repeats. Results go to
bench/results/naming-<time>.json and docs/bench/naming.md. Needs cloud keys (~/Desktop/micromind.txt);
not part of make check.

    uv run python -m bench.naming.run --options gemini-3.8-flash --repeats 1 --limit 40
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import cv2

from bench.naming import composite
from bench.naming.fetch import DATA
from bench.naming.score import VOCAB, summarise
from orchestrator.agent import close_region, identify_context, identify_images, identify_reading, name_closer
from orchestrator.keys import load_keys
from orchestrator.marks import encode_crop, fov_crop
from orchestrator.options import VISION
from orchestrator.prompts import IDENTIFY
from orchestrator.sight import sight_prompt
from orchestrator.vision import make_vision

BENCH = Path(__file__).resolve().parents[1]
DOCS = BENCH.parents[1] / "docs" / "bench" / "naming.md"
CONDITIONS = ("right", "none", "wrong", "grey")


def load(limit: int | None) -> list[dict]:
    manifest = DATA / "manifest.jsonl"
    if not manifest.exists():
        raise SystemExit("No labelled pictures yet: run python -m bench.naming.fetch --yes first")
    items = [json.loads(line) for line in manifest.read_text().splitlines() if line.strip()]
    if limit:
        # Keep every class represented: take round-robin across (set, class).
        by_class: dict = {}
        for item in items:
            by_class.setdefault((item["dataset"], item["label"]), []).append(item)
        ordered, i = [], 0
        while len(ordered) < min(limit, len(items)):
            for group in by_class.values():
                if i < len(group) and len(ordered) < limit:
                    ordered.append(group[i])
            i += 1
        items = ordered
    return items


def frames(item: dict, seed: int, magnification: float = 1.0):
    picture = cv2.imread(str(DATA / item["image"]))
    if picture is None:
        raise ValueError(f"unreadable picture {item['image']}")
    real = composite.eyepiece(picture, seed=seed, magnification=magnification)
    grey = composite.eyepiece(None, seed=seed)
    bbox = item.get("bbox") if isinstance(item.get("bbox"), list) else None
    polygons, box = composite.object_outline(picture.shape[:2], bbox, magnification=magnification)
    return real, grey, polygons, box


async def ask_identify(vision, image, polygons, box, context: str, closeup: str = "single") -> dict:
    """Naming a click as the app does; `closeup` "naming": with successive close-ups (option CLOSEUP)."""
    crop = fov_crop(composite.fov(), image.shape[1], image.shape[0])
    view, close = identify_images(image, polygons, crop, box)
    prompt = IDENTIFY.format(context=context)
    if closeup == "single":
        result = await vision.ask([view, close], prompt)
        data, steps, ms = result.data or {}, [], result.latency_ms
    else:
        data, result, steps, _ = await name_closer(vision.ask, image, view, close_region(image, box), prompt)
        ms = sum(step["ms"] for step in steps)
    reading = identify_reading(data)  # what the app shows: the group when it is not sure
    return {"name": reading["label"], "specific": reading["specific"], "status": reading["status"],
            "maybe": reading["maybe"], "answer": str(data.get("answer") or "")[:300],
            "confidence": reading["confidence"], "rounds": len(steps) or 1,
            "ms": round(ms), "error": result.error or (None if result.data else "unreadable reply")}


async def ask_sight(vision, image, subject: str | None) -> dict:
    crop = fov_crop(composite.fov(), image.shape[1], image.shape[0])
    jpeg, _ = encode_crop(image, crop, max_side=768)
    result = await vision.ask([jpeg], sight_prompt(subject, None), max_output_tokens=900)
    data = result.data or {}
    things = [t for t in data.get("things") or [] if isinstance(t, dict)]
    centre = things[0] if things else {}
    conf = centre.get("confidence")
    return {"name": str(centre.get("name") or ""), "specimen": str(data.get("specimen") or ""),
            "confidence": float(conf) if isinstance(conf, (int, float)) else None,
            "ms": round(result.latency_ms), "error": result.error or (None if result.data else "unreadable reply")}


async def run(options: list[str], items: list[dict], repeats: int, paths: list[str], concurrency: int,
              conditions: tuple[str, ...] = CONDITIONS, magnifications: tuple[float, ...] = (1.0,)) -> list[dict]:
    gate = asyncio.Semaphore(concurrency)
    visions = {o: make_vision(VISION[o].spec) for o in options}
    rows: list[dict] = []

    async def one(option, item, index, condition, repeat, path, magnification):
        vocab = VOCAB.get(item["dataset"], {})
        real, grey, polygons, box = frames(item, seed=index, magnification=magnification)
        image = grey if condition == "grey" else real
        subject = {"right": vocab.get("subject"), "grey": vocab.get("subject"), "wrong": vocab.get("wrong_subject"),
                   "none": None}[condition]
        async with gate:
            try:
                if path in ("identify", "identify+closer"):
                    reply = await ask_identify(visions[option], image, polygons, box, identify_context(subject),
                                               "naming" if path == "identify+closer" else "single")
                else:
                    reply = await ask_sight(visions[option], image, subject)
            except Exception as exc:  # a failed call is a result too
                reply = {"name": "", "error": f"{type(exc).__name__}: {exc}"[:200]}
        name = f"{option}/{path}" + (f"@x{magnification:g}" if magnification != 1.0 else "")
        rows.append({"option": name, "dataset": item["dataset"], "label": item["label"], "magnification": magnification,
                     "image": item["image"], "condition": condition, "repeat": repeat, **reply})

    jobs = [one(o, item, i, c, r, p, m) for o in options for p in paths for m in magnifications
            for i, item in enumerate(items) for c in conditions for r in range(repeats)]
    done = 0
    for future in asyncio.as_completed(jobs):
        await future
        done += 1
        if done % 25 == 0 or done == len(jobs):
            print(f"  {done}/{len(jobs)}", flush=True)
    return rows


def report(summary: dict, meta: dict) -> str:
    lines = ["# Naming bench", "",
             f"Run {meta['at']}: {meta['pictures']} labelled pictures from {', '.join(meta['sets'])}, "
             f"composited into an eyepiece circle; {meta['repeats']} repeat(s). Generated by `make naming-bench` "
             "(services/bench/naming/); the full rows are in the JSON next to it.", "",
             "Conditions: told the right specimen, nothing, the wrong specimen, or a grey disc with the right specimen.", "",
             "Fine: the exact name, over sets whose classes are names (blood cells); Pap smear classes are "
             "diagnostic grades, so they count only for the coarse name (squamous epithelial cell).", "",
             "| Option / path | Condition | n | Fine | Coarse | Abstained | ECE | p50 ms |", "|---|---|---|---|---|---|---|---|"]
    pct = lambda v: f"{v:.0%}" if isinstance(v, (int, float)) else "-"  # noqa: E731
    for option, by_condition in summary.items():
        for condition in CONDITIONS:
            s = by_condition.get(condition)
            if s:
                lines.append(f"| {option} | {condition} | {s['n']} | {pct(s['fine'])} | {pct(s['coarse'])} | "
                             f"{pct(s['abstained'])} | {s['ece'] if s['ece'] is not None else '-'} | {s['p50Ms']} |")
    status_rows = [(o, s) for o, by in summary.items() for c, s in by.items() if c == "right" and isinstance(s, dict) and s.get("byStatus")]
    if status_rows:
        lines += ["", "## What the app showed (right context)", "",
                  "| Option / path | Shown as | Share | Right | The specific name it held back was right |", "|---|---|---|---|---|"]
        for option, s in status_rows:
            for status, b in s["byStatus"].items():
                lines.append(f"| {option} | {status} | {pct(b['share'])} | {pct(b['right'])} | {pct(b['specificRight'])} |")
    lines += ["", "## By labelled set (right context)", "", "| Option / path | Set | n | Fine | Coarse |", "|---|---|---|---|---|"]
    for option, by_condition in summary.items():
        for name, s in (by_condition.get("right") or {}).get("bySet", {}).items():
            lines.append(f"| {option} | {name} | {s['n']} | {pct(s['fine'])} | {pct(s['coarse'])} |")
    lines += ["", "## Context leak", "", "Compare the change rates with consistency (the same picture and context "
              "asked again): a name that changes as often with the same words is noise, not leak.", "",
              "| Option / path | Name changed with no context | with the wrong context | "
              "grey disc named from the words | consistency |", "|---|---|---|---|---|"]
    for option, by_condition in summary.items():
        leak = by_condition["contextLeak"]
        lines.append(f"| {option} | {pct(leak['changedWithoutContext'])} | {pct(leak['changedWithWrongContext'])} | "
                     f"{pct(leak['greyImageNamedFromContext'])} | {pct(by_condition['consistency'])} |")
    lines += ["", "## Per class (right context, fine)", ""]
    for option, by_condition in summary.items():
        right = by_condition.get("right")
        if right:
            cells = ", ".join(f"{k} {v:.0%}" for k, v in right["perClass"].items())
            lines.append(f"- **{option}**: {cells}")
    lines += ["", "## Most common wrong names (right context)", ""]
    for option, by_condition in summary.items():
        right = by_condition.get("right")
        if right and right["confusions"]:
            cells = "; ".join(f"{k} → {', '.join(f'{n} ({c})' for n, c in v.items())}" for k, v in right["confusions"].items())
            lines.append(f"- **{option}**: {cells}")
    return "\n".join(lines) + "\n"


def closer_report(title: str, summary: dict, rows: list[dict], meta: dict, results: str) -> str:
    """A compact section for comparing arms (successive close-ups, magnifications), appended to the doc."""
    pct = lambda v: f"{v:.0%}" if isinstance(v, (int, float)) else "-"  # noqa: E731
    lines = [f"## {title} (run {meta['at']})", "",
             f"{meta['pictures']} pictures, conditions {', '.join(meta['conditions'])}, magnifications "
             f"{', '.join(f'x{m:g}' for m in meta['magnifications'])}, {meta['repeats']} repeat(s). "
             f"`bench/results/{results}`.", "",
             "| Option / path | Condition | n | Exact | Coarse | Not sure | p50 ms | p90 ms | Asked closer | Rounds (mean) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for option, by_condition in summary.items():
        for condition in CONDITIONS:
            s = by_condition.get(condition)
            if not s:
                continue
            mine = [r for r in rows if r["option"] == option and r["condition"] == condition and not r.get("error")]
            ms = sorted(r["ms"] for r in mine if r.get("ms"))
            p90 = ms[int(0.9 * (len(ms) - 1))] if ms else None
            closer = [r for r in mine if (r.get("rounds") or 1) > 1]
            rounds = sum(r.get("rounds") or 1 for r in mine) / len(mine) if mine else None
            unsure = sum(1 for r in mine if r.get("status") == "unsure") / len(mine) if mine else None
            lines.append(f"| {option} | {condition} | {s['n']} | {pct(s['fine'])} | {pct(s['coarse'])} | {pct(unsure)} | "
                         f"{s['p50Ms']} | {p90} | {pct(len(closer) / len(mine) if mine else None)} | "
                         f"{rounds:.2f} |" if rounds is not None else "| - |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--options", default="gemini-3.8-flash", help="comma-separated VISION options")
    parser.add_argument("--paths", default="identify,sight", help="identify (naming a click) and/or sight (the running look)")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None, help="at most this many pictures, every class kept")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--conditions", default=",".join(CONDITIONS), help="which of: " + ", ".join(CONDITIONS))
    parser.add_argument("--rescore", default="", help="re-score a saved results JSON (no model calls)")
    parser.add_argument("--magnifications", default="1", help="comma-separated: below 1 the things are smaller in the view")
    parser.add_argument("--append", default="", help="append a section with this title to docs/bench/naming.md "
                                                     "instead of rewriting it")
    args = parser.parse_args()
    if args.rescore:
        saved = json.loads(Path(args.rescore).read_text())
        summary = summarise(saved["rows"])
        Path(args.rescore).write_text(json.dumps({**saved, "summary": summary}, indent=1))
        DOCS.write_text(report(summary, saved["meta"]))
        print(f"re-scored {args.rescore} -> {DOCS.relative_to(BENCH.parents[1])}")
        return 0
    load_keys()
    options = [o for o in args.options.split(",") if o]
    unknown = [o for o in options if o not in VISION]
    if unknown:
        raise SystemExit(f"unknown vision options: {', '.join(unknown)} (one of {', '.join(VISION)})")
    items = load(args.limit)
    started = time.strftime("%Y%m%d-%H%M%S")
    conditions = tuple(c for c in args.conditions.split(",") if c in CONDITIONS)
    print(f"{len(items)} pictures x {len(conditions)} conditions x {args.repeats} repeat(s) x {len(options)} option(s)")
    magnifications = tuple(float(m) for m in args.magnifications.split(",") if m)
    rows = asyncio.run(run(options, items, args.repeats, [p for p in args.paths.split(",") if p], args.concurrency, conditions,
                           magnifications))
    summary = summarise(rows)
    meta = {"at": started, "pictures": len(items), "sets": sorted({i["dataset"] for i in items}), "repeats": args.repeats,
            "conditions": list(conditions), "magnifications": list(magnifications)}
    out = BENCH / "results" / f"naming-{started}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"meta": meta, "summary": summary, "rows": rows}, indent=1))
    if args.append:
        with DOCS.open("a") as f:
            f.write("\n" + closer_report(args.append, summary, rows, meta, out.name))
    else:
        DOCS.write_text(report(summary, meta))
    print(f"wrote {out.relative_to(BENCH.parent)} and {DOCS.relative_to(BENCH.parents[1])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
