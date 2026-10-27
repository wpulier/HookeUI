"""MedSigLIP's zero-shot ranking for naming a click (orchestrator/zeroshot.py, option ZEROSHOT): how
often is its first name right on its own, how fast is it, and does telling the naming model help or
hurt, on blood cells and on the other specimen of the bench (Pap smear cells)?

Uses the naming bench's labelled pictures (bench/naming, `python -m bench.naming.fetch --yes`), each
put into an eyepiece circle the way the app sees it and cropped around its outline exactly as the app
crops for MedSigLIP (zeroshot.MARGIN, 448 px).

  alone    MedSigLIP as a classifier, in process: its first name against the truth (exact and coarse,
           scored as the naming bench scores names), top-3, per class, what it says most, for each
           candidate list: the subject's own ("subject"), and every list at once ("always")
  latency  one crop at a time in process (the Mac's GPU, float16), and the heavy GPU worker's
           `zeroshot` op round trip (what the app waits for; --no-worker skips it)
  naming   (cloud calls, --naming) naming a click (IDENTIFY, told the right specimen) without the
           line, with the subject's line, and with the every-list line, on every picture: how often
           the line turns a wrong name right or a right name wrong, per set; `--arms off,names`
           runs a control told the subject's names as a plain list, without MedSigLIP's ranking

    PYTHONPATH=. python -m bench.zeroshot_bench --naming --repeats 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections import Counter
from pathlib import Path

import cv2

from bench.naming import composite
from bench.naming.fetch import DATA
from bench.naming.run import load
from bench.naming.score import VOCAB, judge
from orchestrator import zeroshot
from orchestrator.agent import identify_context, identify_images, identify_reading
from orchestrator.marks import fov_crop
from orchestrator.prompts import IDENTIFY
from orchestrator.taught import crop_for, encode

BENCH = Path(__file__).resolve().parent
BLOOD, PAP = "acevedo_et_al_2020", "hussain_et_al_2019"
ARMS = ("off", "subject", "always")
# A control, not an option: the subject's names as a plain list, no ranking. Does MedSigLIP's ranking
# add anything beyond being told which names fit the specimen?
CONTROL = "names"


def p(values, q):
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * len(values)))], 1) if values else None


def pct(part, whole):
    return round(part / whole, 3) if whole else None


def frames(items):
    out = []
    for i, item in enumerate(items):
        picture = cv2.imread(str(DATA / item["image"]))
        frame = composite.eyepiece(picture, seed=i)
        polygons, box = composite.object_outline(picture.shape[:2], item.get("bbox") if isinstance(item.get("bbox"), list) else None)
        out.append((frame, polygons, box))
    return out


def rankings(model, items, framed) -> dict[str, list]:
    """Per arm, each picture's Ranking (None where the arm tells nothing)."""
    crops = [crop_for(frame, box, margin=zeroshot.MARGIN, side=zeroshot.SIDE) for frame, _, box in framed]
    out: dict[str, list] = {}
    for arm in ("subject", "always"):
        rows = []
        for item, crop in zip(items, crops):
            c = zeroshot.candidates_for(VOCAB[item["dataset"]]["subject"], arm)
            ranked = model.rank([crop], list(c.names), c.template)[0] if c is not None else []
            rows.append(zeroshot.Ranking(c, tuple(ranked)) if c is not None else None)
        out[arm] = rows
    return out


def alone(items, ranks: dict[str, list]) -> dict:
    out = {}
    for arm, rows in ranks.items():
        for dataset in (BLOOD, PAP):
            idx = [i for i, it in enumerate(items) if it["dataset"] == dataset and rows[i] is not None]
            if not idx:
                continue
            verdicts = [judge(dataset, items[i]["label"], rows[i].ranked[0][0]) for i in idx]
            top3 = [any(judge(dataset, items[i]["label"], n)["fine"] for n, _ in rows[i].ranked[:3]) for i in idx]
            per_class: dict[str, list] = {}
            for i, v in zip(idx, verdicts):
                per_class.setdefault(items[i]["label"], []).append(bool(v["fine"] if v["fine"] is not None else v["coarse"]))
            shares_right = [rows[i].ranked[0][1] for i, v in zip(idx, verdicts) if v["fine"]]
            shares_wrong = [rows[i].ranked[0][1] for i, v in zip(idx, verdicts) if v["fine"] is False]
            out[f"{arm}/{dataset}"] = {
                "n": len(idx),
                "exact": pct(sum(1 for v in verdicts if v["fine"]), len(idx)) if dataset == BLOOD else None,
                "coarse": pct(sum(1 for v in verdicts if v["coarse"]), len(idx)),
                "top3": pct(sum(top3), len(idx)) if dataset == BLOOD else None,
                "perClass": {k: pct(sum(v), len(v)) for k, v in sorted(per_class.items())},
                "says": Counter(rows[i].ranked[0][0] for i in idx).most_common(5),
                "topShareRightP50": round(statistics.median(shares_right), 2) if shares_right else None,
                "topShareWrongP50": round(statistics.median(shares_wrong), 2) if shares_wrong else None}
    return out


def latency(model, framed) -> dict:
    crops = [crop_for(frame, box, margin=zeroshot.MARGIN, side=zeroshot.SIDE) for frame, _, box in framed[:40]]
    c = zeroshot.by_id("blood")
    for crop in crops[:5]:
        model.rank([crop], list(c.names), c.template)  # warm up (MPS compiles its graphs)
    times = []
    for crop in crops:
        if model.device.type == "mps":
            model.torch.mps.synchronize()
        t0 = time.perf_counter()
        model.rank([crop], list(c.names), c.template)
        times.append((time.perf_counter() - t0) * 1000)
    return {"device": str(model.device), "dtype": str(model.dtype), "p50Ms": p(times, 0.5), "p90Ms": p(times, 0.9), "n": len(times)}


async def worker_latency(framed) -> dict:
    """The app's path: JPEG crops over the pipe to the heavy worker's `zeroshot` op."""
    from orchestrator.gpu_client import GpuClient

    client = GpuClient("zeroshot-bench-heavy", warm=False)
    t0 = time.perf_counter()
    await client.start()
    start_s = time.perf_counter() - t0
    jpegs = [encode(crop_for(frame, box, margin=zeroshot.MARGIN, side=zeroshot.SIDE)) for frame, _, box in framed[:40]]
    c = zeroshot.by_id("blood")
    t0 = time.perf_counter()
    await client.call("zeroshot", timeout=300, jpegs=[jpegs[0]], names=list(c.names), template=c.template)
    first_s = time.perf_counter() - t0
    times, inside = [], []
    for j in jpegs:
        t0 = time.perf_counter()
        r = await client.call("zeroshot", timeout=60, jpegs=[j], names=list(c.names), template=c.template)
        times.append((time.perf_counter() - t0) * 1000)
        inside.append(r["ms"])
    client.stop()
    return {"device": client.device, "startS": round(start_s, 2), "firstCallS": round(first_s, 2),
            "p50Ms": p(times, 0.5), "p90Ms": p(times, 0.9), "workerP50Ms": p(inside, 0.5), "n": len(times)}


async def naming(items, framed, ranks: dict[str, list], option: str, repeats: int, concurrency: int = 8,
                 arms: tuple[str, ...] = ARMS) -> dict:
    from orchestrator.keys import load_keys
    from orchestrator.options import VISION
    from orchestrator.vision import make_vision

    load_keys()
    vision = make_vision(VISION[option].spec)
    gate = asyncio.Semaphore(concurrency)
    rows = []

    async def one(i: int, arm: str, repeat: int):
        item = items[i]
        frame, polygons, box = framed[i]
        view, close = identify_images(frame, polygons, fov_crop(composite.fov(), frame.shape[1], frame.shape[0]), box)
        ranking = ranks[arm][i] if arm in ranks else None
        line = zeroshot.context_line(ranking)
        if arm == CONTROL:
            c = zeroshot.candidates_for(VOCAB[item["dataset"]]["subject"], "subject")
            line = f"Names that fit {c.specimen}: {', '.join(c.names)} (a list, not a ranking of this one)."
        # The line sits where the app puts it: after the specimen (agent.identify_context).
        context = identify_context(VOCAB[item["dataset"]]["subject"], zeroshot=line)
        async with gate:
            result = await vision.ask([view, close], IDENTIFY.format(context=context))
        reading = identify_reading(result.data or {})
        verdict = judge(item["dataset"], item["label"], reading["label"])
        top = ranking.ranked[0][0] if ranking is not None else None
        rows.append({"image": item["image"], "dataset": item["dataset"], "label": item["label"], "arm": arm, "repeat": repeat,
                     "told": bool(line), "name": reading["label"], "specific": reading["specific"], "status": reading["status"],
                     "top": top, "topRight": bool(judge(item["dataset"], item["label"], top)["fine"]) if top else None,
                     "ms": round(result.latency_ms), "error": result.error,
                     "exact": verdict["fine"], "coarse": verdict["coarse"]})

    jobs = [one(i, arm, r) for r in range(repeats) for i in range(len(items)) for arm in arms]
    done = 0
    for future in asyncio.as_completed(jobs):
        await future
        done += 1
        if done % 60 == 0 or done == len(jobs):
            print(f"  {done}/{len(jobs)}", flush=True)
    return {"option": option, "repeats": repeats, "arms": list(arms), "summary": summarise_naming(rows), "rows": rows}


def summarise_naming(rows: list[dict]) -> dict:
    out = {}
    by = {(r["image"], r["arm"], r["repeat"]): r for r in rows}
    for dataset in (BLOOD, PAP, "all"):
        mine = [r for r in rows if dataset == "all" or r["dataset"] == dataset]
        for arm in (*ARMS, CONTROL):
            arm_rows = [r for r in mine if r["arm"] == arm and not r["error"]]
            if not arm_rows:
                continue
            graded = [r for r in arm_rows if r["exact"] is not None]
            s = {"n": len(arm_rows), "told": pct(sum(r["told"] for r in arm_rows), len(arm_rows)),
                 "exact": pct(sum(1 for r in graded if r["exact"]), len(graded)) if graded else None,
                 "coarse": pct(sum(1 for r in arm_rows if r["coarse"]), len(arm_rows)),
                 "unsure": pct(sum(1 for r in arm_rows if r["status"] == "unsure"), len(arm_rows)),
                 "p50Ms": p([r["ms"] for r in arm_rows], 0.5)}
            if arm != "off":
                pairs = [(by.get((r["image"], "off", r["repeat"])), r) for r in arm_rows]
                pairs = [(a, b) for a, b in pairs if a is not None and not a["error"]]
                key = "exact" if dataset == BLOOD else "coarse"
                s["helped"] = sum(1 for a, b in pairs if not a[key] and b[key])
                s["hurt"] = sum(1 for a, b in pairs if a[key] and not b[key])
                s["by"] = key
                wrong_top = [b for _, b in pairs if b["top"] and b["topRight"] is False]
                s["tookAWrongTop"] = sum(1 for b in wrong_top if b["top"] in (b["specific"] or b["name"] or "").lower())
                s["wrongTop"] = len(wrong_top)
            out[f"{dataset}/{arm}"] = s
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--naming", action="store_true", help="name every picture with and without the line (cloud calls)")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--option", default="gemini-3.5-flash", help="the vision model naming clicks")
    parser.add_argument("--no-worker", action="store_true", help="skip the heavy worker round trip")
    parser.add_argument("--arms", default=",".join(ARMS), help=f"naming arms, of {', '.join(ARMS)} and the control {CONTROL!r}")
    args = parser.parse_args()
    import torch

    from gpu_worker.zeroshot import ZeroShot
    from orchestrator.keys import load_keys

    load_keys()  # HF_TOKEN, for a gated model not yet in the cache (never printed)
    items = load(None)
    framed = frames(items)
    out: dict = {"at": time.strftime("%Y-%m-%d %H:%M"), "pictures": len(items)}
    t0 = time.perf_counter()
    model = ZeroShot("mps" if torch.backends.mps.is_available() else "cpu")
    out["loadS"] = round(time.perf_counter() - t0, 1)
    ranks = rankings(model, items, framed)
    out["alone"] = alone(items, ranks)
    out["latency"] = latency(model, framed)
    del model
    if not args.no_worker:
        out["worker"] = asyncio.run(worker_latency(framed))
    if args.naming:
        arms = tuple(a for a in args.arms.split(",") if a in (*ARMS, CONTROL))
        out["naming"] = asyncio.run(naming(items, framed, ranks, args.option, args.repeats, arms=arms))
    path = BENCH / "results" / f"zeroshot-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=1))
    summary = {k: v for k, v in out.items() if k != "naming"}
    if "naming" in out:
        summary["naming"] = out["naming"]["summary"]
    print(json.dumps(summary, indent=1))
    print(f"wrote {path.relative_to(BENCH.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
