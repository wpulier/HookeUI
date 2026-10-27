"""Seen one like this before today? (orchestrator/seen.py, option SEEN): how alike do DINOv2 crops of
the same kind of thing look, against different kinds, and from which similarity is "the earlier
outline most like this one" the same kind often enough to tell naming?

Uses the naming bench's labelled pictures (bench/naming, `python -m bench.naming.fetch --yes`), each
put into an eyepiece circle the way the app sees it and cropped as the app crops (taught.crop_for),
embedded as the app embeds (gpu_worker/embed.py: DINOv2's class token, or BioCLIP 2 with --embed).

  pairs     every pair of pictures: the similarity of the same kind, of different kinds of the same
            specimen (blood cell against blood cell) and of different specimens (blood against Pap)
  day       a day of clicks in random order: each one is compared with the earlier ones, as naming
            is; for each threshold, how often the most alike earlier outline, when above it, is the
            same kind (precision), and how often a click that has an earlier one of its kind gets a
            line naming one of its kind (recall). Days of 30 clicks and of all 180, 20 orders each.
  naming    (cloud calls, --naming N) naming a click (IDENTIFY) with and without the seen line, on N
            pictures that have an earlier one above the threshold, the earlier one carrying the name
            the model gave it (as in the app): does the line help or hurt?

    PYTHONPATH=. python -m bench.seen_bench [--embed bioclip-2] [--naming 60]

Similarities of two models are on different scales, so each model's threshold is fitted on its own
(`fit`: the lowest one whose precision reaches the target on days of 30 and of 180 clicks).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from pathlib import Path

import numpy as np

from bench.naming.run import load
from bench.taught_bench import crops, p
from orchestrator import seen, taught

BENCH = Path(__file__).resolve().parent
THRESHOLDS = [round(0.60 + 0.005 * i, 3) for i in range(80)]  # 0.600 ... 0.995 (BioCLIP 2's scale is narrow)


def kinds(items) -> list[tuple[str, str]]:
    return [(item["dataset"], item["label"]) for item in items]


def pairs(vectors: np.ndarray, kind: list) -> dict:
    sims = vectors @ vectors.T
    same, other_kind, other_specimen = [], [], []
    n = len(kind)
    for i in range(n):
        for j in range(i + 1, n):
            s = float(sims[i, j])
            if kind[i] == kind[j]:
                same.append(s)
            elif kind[i][0] == kind[j][0]:
                other_kind.append(s)
            else:
                other_specimen.append(s)

    # p() rounds to one decimal place: these are similarities, so round them ourselves.
    def q(values, at):
        values = sorted(values)
        return round(values[min(len(values) - 1, int(at * len(values)))], 3)

    out = {}
    for name, values in (("sameKind", same), ("otherKindSameSpecimen", other_kind), ("otherSpecimen", other_specimen)):
        out[name] = {"n": len(values), "p10": q(values, 0.1), "p50": q(values, 0.5), "p90": q(values, 0.9),
                     "p99": q(values, 0.99), "max": round(max(values), 3)}
    # Pairwise precision: of the pairs at or above t, the share that are the same kind.
    out["pairPrecision"] = {}
    for t in THRESHOLDS:
        a = sum(1 for s in same if s >= t)
        b = sum(1 for s in other_kind + other_specimen if s >= t)
        out["pairPrecision"][f"{t:.3f}"] = {"precision": round(a / (a + b), 3) if a + b else None,
                                            "sameKindAbove": round(a / len(same), 3)}
    return out


def day(vectors: np.ndarray, kind: list, size: int, orders: int) -> dict:
    """Days of `size` clicks: each click against the earlier ones (naming's question)."""
    sims = vectors @ vectors.T
    per_t = {t: {"fired": 0, "right": 0, "had": 0, "found": 0, "otherSpecimen": 0} for t in THRESHOLDS}
    clicks = 0
    for seed in range(orders):
        order = list(range(len(kind)))
        random.Random(seed).shuffle(order)
        order = order[:size]
        for pos in range(1, len(order)):
            i, earlier = order[pos], order[:pos]
            row = sims[i, earlier]
            best = int(np.argmax(row))
            j, s = earlier[best], float(row[best])
            had = any(kind[e] == kind[i] for e in earlier)
            clicks += 1
            for t in THRESHOLDS:
                c = per_t[t]
                c["had"] += had
                if s >= t:
                    c["fired"] += 1
                    c["right"] += kind[j] == kind[i]
                    c["found"] += kind[j] == kind[i]
                    c["otherSpecimen"] += kind[j][0] != kind[i][0]
    out = {"clicks": clicks}
    for t, c in per_t.items():
        out[f"{t:.3f}"] = {"precision": round(c["right"] / c["fired"], 3) if c["fired"] else None,
                           "recall": round(c["found"] / c["had"], 3) if c["had"] else None,
                           "fires": round(c["fired"] / clicks, 3),
                           "otherSpecimen": round(c["otherSpecimen"] / c["fired"], 3) if c["fired"] else None}
    return out


def fit(day30: dict, day180: dict, target: float = 0.92) -> float | None:
    """The lowest threshold from which the most alike earlier outline was the same kind at least
    `target` of the time on both days, and at every threshold above it that fired."""
    for t in THRESHOLDS:
        rows = [d[f"{u:.3f}"] for d in (day30, day180) for u in THRESHOLDS if u >= t]
        if all(r["precision"] is None or r["precision"] >= target for r in rows) and any(r["precision"] for r in rows):
            return t
    return None


def floor(pair: dict, vectors: np.ndarray, kind: list, share: float = 0.98) -> float | None:
    """The lowest similarity from which at least `share` of pairs were the same specimen (the list's floor)."""
    sims = vectors @ vectors.T
    n = len(kind)
    pairs_ = [(float(sims[i, j]), kind[i][0] == kind[j][0]) for i in range(n) for j in range(i + 1, n)]
    for t in THRESHOLDS:
        above = [same for s, same in pairs_ if s >= t]
        if above and sum(above) / len(above) >= share:
            return t
    return None


async def naming(items, vectors, n: int, option: str, threshold: float, concurrency: int = 6,
                 confirmed: bool = False, model: str = "dinov2-small") -> dict:
    """IDENTIFY with and without the seen line, on clicks whose most alike earlier one is above the
    threshold (a day of all 180 in one random order). As in the app, the earlier one carries the
    name the model gave it (named without any line first), not its true kind; with `confirmed`, also
    as if the user had named the earlier one (its true kind)."""
    import cv2

    from bench.naming import composite
    from bench.naming.fetch import DATA
    from bench.naming.score import VOCAB, judge
    from orchestrator.agent import identify_context, identify_images, identify_reading
    from orchestrator.keys import load_keys
    from orchestrator.marks import fov_crop
    from orchestrator.options import VISION
    from orchestrator.prompts import IDENTIFY
    from orchestrator.vision import make_vision

    load_keys()
    vision = make_vision(VISION[option].spec)
    sims = vectors @ vectors.T
    order = list(range(len(items)))
    random.Random(7).shuffle(order)
    cases = []
    for pos in range(1, len(order)):
        i, earlier = order[pos], order[:pos]
        row = sims[i, earlier]
        best = int(np.argmax(row))
        if float(row[best]) >= threshold:
            cases.append((i, earlier[best], float(row[best])))
    cases = cases[:n]
    gate = asyncio.Semaphore(concurrency)
    named: dict[tuple[int, str], dict] = {}

    async def name(i: int, line: str = "") -> dict:
        item = items[i]
        picture = cv2.imread(str(DATA / item["image"]))
        frame = composite.eyepiece(picture, seed=i)
        polygons, box = composite.object_outline(picture.shape[:2], None)
        view, close = identify_images(frame, polygons, fov_crop(composite.fov(), frame.shape[1], frame.shape[0]), box)
        context = identify_context(VOCAB[item["dataset"]]["subject"], seen=line)
        async with gate:
            result = await vision.ask([view, close], IDENTIFY.format(context=context))
        reading = identify_reading(result.data or {})
        return {"name": reading["label"], "status": reading["status"], "ms": round(result.latency_ms), "error": result.error,
                "right": bool(judge(item["dataset"], item["label"], reading["label"])["fine"])}

    plain = sorted({i for i, _, _ in cases} | {j for _, j, _ in cases})
    for i, got in zip(plain, await asyncio.gather(*(name(i) for i in plain))):
        named[(i, "")] = got
    rows = []

    async def with_line(i: int, j: int, s: float):
        before = named[(j, "")]
        earlier = seen.Kept(id="s1", object_id="o1", label=before["name"], key=before["name"], status=before["status"],
                            thread="t1", vector=[], crop="", at=time.time() - 3600, model=model)
        got = await name(i, seen.context_line(seen.Similar(earlier, s)))
        row = {"image": items[i]["image"], "label": items[i]["label"], "earlier": items[j]["label"],
               "earlierName": before["name"], "earlierNameRight": before["right"], "similarity": round(s, 3),
               "without": named[(i, "")], "with": got}
        if confirmed:
            # The same, had the user named the earlier one (its true kind, "confirmed").
            truth = seen.Kept(id="s1", object_id="o1", label=items[j]["label"], key=items[j]["label"], status="confirmed",
                              thread="t1", vector=[], crop="", at=time.time() - 3600, model=model)
            row["withConfirmed"] = await name(i, seen.context_line(seen.Similar(truth, s)))
        rows.append(row)

    await asyncio.gather(*(with_line(i, j, s) for i, j, s in cases))
    k = len(rows)
    out = {}
    if confirmed and k:
        out = {"withConfirmed": round(sum(r["withConfirmed"]["right"] for r in rows) / k, 3),
               "helpedConfirmed": sum(1 for r in rows if not r["without"]["right"] and r["withConfirmed"]["right"]),
               "hurtConfirmed": sum(1 for r in rows if r["without"]["right"] and not r["withConfirmed"]["right"])}
    return {"option": option, "threshold": threshold, "n": k, "calls": len(plain) + k * (2 if confirmed else 1), **out,
            "earlierSameKind": round(sum(r["earlier"] == r["label"] for r in rows) / k, 3) if k else None,
            "earlierNameRight": round(sum(r["earlierNameRight"] for r in rows) / k, 3) if k else None,
            "without": round(sum(r["without"]["right"] for r in rows) / k, 3) if k else None,
            "with": round(sum(r["with"]["right"] for r in rows) / k, 3) if k else None,
            "helped": sum(1 for r in rows if not r["without"]["right"] and r["with"]["right"]),
            "hurt": sum(1 for r in rows if r["without"]["right"] and not r["with"]["right"]),
            "p50MsWithout": p([r["without"]["ms"] for r in rows], 0.5), "p50MsWith": p([r["with"]["ms"] for r in rows], 0.5),
            "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders", type=int, default=20, help="random orders of the day's clicks")
    parser.add_argument("--naming", type=int, default=0, help="clicks to name with and without the line (2 calls each)")
    parser.add_argument("--confirmed", action="store_true", help="also name with the earlier one named right by the user")
    parser.add_argument("--option", default="gemini-3.5-flash", help="the vision model naming clicks")
    parser.add_argument("--embed", default="dinov2-small", help="the embedding model (option EMBED)")
    parser.add_argument("--threshold", type=float, default=None, help="naming's threshold (default: the model's own)")
    args = parser.parse_args()
    from gpu_worker.embed import embedder

    import torch

    items = load(None)
    kind = kinds(items)
    model = embedder(args.embed, "mps" if torch.backends.mps.is_available() else "cpu")
    vectors = model.vectors(crops(items, taught.MARGIN))
    threshold = args.threshold if args.threshold is not None else seen.threshold(args.embed)
    out: dict = {"at": time.strftime("%Y-%m-%d %H:%M"), "embed": args.embed, "pictures": len(items), "kinds": len(set(kind)),
                 "pairs": pairs(vectors, kind),
                 "day30": day(vectors, kind, 30, args.orders), "day180": day(vectors, kind, len(items), args.orders),
                 "threshold": threshold}
    out["fitted"] = {"threshold": fit(out["day30"], out["day180"]), "listFloor": floor(out["pairs"], vectors, kind)}
    if args.naming:
        out["naming"] = asyncio.run(naming(items, vectors, args.naming, args.option, threshold,
                                             confirmed=args.confirmed, model=args.embed))
    path = BENCH / "results" / f"seen-{args.embed}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=1))
    summary = {k: v for k, v in out.items() if k != "naming"}
    if "naming" in out:
        summary["naming"] = {k: v for k, v in out["naming"].items() if k != "rows"}
    print(json.dumps(summary, indent=1))
    print(f"wrote {path.relative_to(BENCH.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
