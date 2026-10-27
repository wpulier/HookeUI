"""Labels the user teaches (orchestrator/taught.py, option TAUGHT): how fast is the embedding, how
often is the nearest taught name right, and does telling the naming model help or hurt?

Uses the naming bench's labelled pictures (bench/naming, `python -m bench.naming.fetch --yes`),
each put into an eyepiece circle the way the app sees it and cropped around its outline exactly
as the app crops (taught.crop_for).

  latency   the embedding model (option EMBED: DINOv2 small, or BioCLIP 2 with --embed) on the CPU
            and on the Mac's GPU (MPS), one crop at a time, and the heavy GPU worker's `embed` op
            round trip (what the app waits for)
  nearest   teach 5 examples per class from part of the set, match the rest; top-1 accuracy over
            several random splits, for the ways of matching (1-NN, mean of the 3 nearest) and
            crop margins; and the similarity of right matches, wrong ones, and pictures of a
            kind never taught (what the studio's number means)
  naming    (cloud calls, --naming N) naming a click (IDENTIFY) with and without the taught line,
            on N blood-cell pictures not taught: how often the line turns a wrong name right or
            a right name wrong
  zeroshot  (BioCLIP 2 only, --zeroshot) its text tower: which of the specimen's names is each
            picture closest to, with no examples taught (a second opinion for naming?)

    PYTHONPATH=. python -m bench.taught_bench [--embed bioclip-2] [--zeroshot] --naming 80
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from pathlib import Path

import numpy as np

from bench.naming import composite
from bench.naming.run import load
from bench.naming.score import VOCAB, judge
from bench.naming.fetch import DATA
from orchestrator import taught
from orchestrator.agent import identify_context, identify_images, identify_reading
from orchestrator.marks import fov_crop
from orchestrator.prompts import IDENTIFY

BENCH = Path(__file__).resolve().parent
TEACH = 5


def p(values, q):
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * len(values)))], 1) if values else None


def crops(items, margin: float):
    import cv2

    out = []
    for i, item in enumerate(items):
        picture = cv2.imread(str(DATA / item["image"]))
        frame = composite.eyepiece(picture, seed=i)
        _, box = composite.object_outline(picture.shape[:2], item.get("bbox") if isinstance(item.get("bbox"), list) else None)
        out.append(taught.crop_for(frame, box, margin=margin))
    return out


def latency(images, model: str = "dinov2-small") -> dict:
    from gpu_worker.embed import embedder as make

    import torch

    result = {}
    for device in ["cpu", *(["mps"] if torch.backends.mps.is_available() else [])]:
        t0 = time.perf_counter()
        embedder = make(model, device)
        load_s = time.perf_counter() - t0
        for image in images[:5]:
            embedder.vectors([image])  # warm up (MPS compiles its graphs)
        times = []
        for image in images[:60]:
            t0 = time.perf_counter()
            embedder.vectors([image])
            times.append((time.perf_counter() - t0) * 1000)
        result[device] = {"loadS": round(load_s, 2), "p50Ms": p(times, 0.5), "p90Ms": p(times, 0.9), "n": len(times)}
    return result


async def worker_latency(images, model: str = "dinov2-small") -> dict:
    """The app's path: JPEG crops over the pipe to the heavy worker's `embed` op."""
    from orchestrator.gpu_client import GpuClient

    client = GpuClient("taught-bench-heavy", warm=False)
    t0 = time.perf_counter()
    await client.start()
    start_s = time.perf_counter() - t0
    jpegs = [taught.encode(i) for i in images[:60]]
    t0 = time.perf_counter()
    await client.call("embed", timeout=300, jpegs=[jpegs[0]], model=model)
    first_s = time.perf_counter() - t0
    times, inside = [], []
    for j in jpegs:
        t0 = time.perf_counter()
        r = await client.call("embed", timeout=30, jpegs=[j], model=model)
        times.append((time.perf_counter() - t0) * 1000)
        inside.append(r["ms"])
    client.stop()
    return {"device": client.device, "startS": round(start_s, 2), "firstCallS": round(first_s, 2),
            "p50Ms": p(times, 0.5), "p90Ms": p(times, 0.9), "workerP50Ms": p(inside, 0.5), "n": len(times)}


def splits(items, seeds: int):
    by_class: dict = {}
    for i, item in enumerate(items):
        by_class.setdefault((item["dataset"], item["label"]), []).append(i)
    for seed in range(seeds):
        rng = random.Random(seed)
        teach, test = [], []
        for idx in by_class.values():
            idx = idx[:]
            rng.shuffle(idx)
            teach += idx[:TEACH]
            test += idx[TEACH:]
        yield teach, test


def examples_of(items, vectors, indices, labels=None, model: str = taught.DEFAULT_MODEL):
    return [taught.Example(id=f"t{i}", label=(labels or {}).get(items[i]["label"], items[i]["label"]),
                           key=items[i]["label"], vector=list(map(float, vectors[i])), crop="", at=float(n), model=model)
            for n, i in enumerate(indices)]


def nearest_accuracy(items, vectors, seeds: int, k: int, sets=None) -> dict:
    right, sims_right, sims_wrong = [], [], []
    for teach, test in splits(items, seeds):
        teach = [i for i in teach if sets is None or items[i]["dataset"] in sets]
        test = [i for i in test if sets is None or items[i]["dataset"] in sets]
        ex = examples_of(items, vectors, teach)
        for i in test:
            m = taught.nearest(vectors[i], ex, k=k)
            ok = m.label == items[i]["label"]
            right.append(ok)
            (sims_right if ok else sims_wrong).append(m.similarity)
    return {"top1": round(sum(right) / len(right), 3), "n": len(right),
            "simRightP50": round(statistics.median(sims_right), 3) if sims_right else None,
            "simWrongP50": round(statistics.median(sims_wrong), 3) if sims_wrong else None}


def never_taught(items, vectors, k: int) -> dict:
    """Teach only blood cells; how alike do Pap smear cells look to them?"""
    teach, _ = next(splits(items, 1))
    ex = examples_of(items, vectors, [i for i in teach if items[i]["dataset"] == "acevedo_et_al_2020"])
    sims = [taught.nearest(vectors[i], ex, k=k).similarity for i in range(len(items)) if items[i]["dataset"] != "acevedo_et_al_2020"]
    return {"simP50": round(statistics.median(sims), 3), "simP90": p(sims, 0.9), "simMax": round(max(sims), 3), "n": len(sims)}


async def naming(items, vectors, n: int, option: str, concurrency: int = 6, model: str = "dinov2-small") -> dict:
    """IDENTIFY with and without the taught line, on blood-cell pictures that were not taught."""
    import cv2

    from orchestrator.keys import load_keys
    from orchestrator.options import VISION
    from orchestrator.vision import make_vision

    load_keys()
    vision = make_vision(VISION[option].spec)
    teach, test = next(splits(items, 1))
    blood = "acevedo_et_al_2020"
    ex = examples_of(items, vectors, [i for i in teach if items[i]["dataset"] == blood], model=model)
    test = [i for i in test if items[i]["dataset"] == blood][:n]
    subject = VOCAB[blood]["subject"]
    gate = asyncio.Semaphore(concurrency)
    rows = []

    async def one(i: int, with_line: bool):
        item = items[i]
        picture = cv2.imread(str(DATA / item["image"]))
        frame = composite.eyepiece(picture, seed=i)
        polygons, box = composite.object_outline(picture.shape[:2], None)
        view, close = identify_images(frame, polygons, fov_crop(composite.fov(), frame.shape[1], frame.shape[0]), box)
        match = taught.nearest(vectors[i], ex, model=model)  # the line carries the model's calibration
        context = identify_context(subject)
        if with_line:
            context = "\n".join([context, taught.context_line(match)])
        async with gate:
            result = await vision.ask([view, close], IDENTIFY.format(context=context))
        reading = identify_reading(result.data or {})
        rows.append({"image": item["image"], "label": item["label"], "withLine": with_line, "name": reading["label"],
                     "specific": reading["specific"], "status": reading["status"], "taught": match.label,
                     "similarity": round(match.similarity, 3), "ms": round(result.latency_ms), "error": result.error,
                     "right": bool(judge(blood, item["label"], reading["label"])["fine"])})

    await asyncio.gather(*(one(i, w) for i in test for w in (False, True)))
    by = {(r["image"], r["withLine"]): r for r in rows}
    pairs = [(by[(items[i]["image"], False)], by[(items[i]["image"], True)]) for i in test]
    taught_right = [b["taught"] == b["label"] for _, b in pairs]
    helped = sum(1 for a, b in pairs if not a["right"] and b["right"])
    hurt = sum(1 for a, b in pairs if a["right"] and not b["right"])
    followed_wrong = sum(1 for a, b in pairs if b["taught"] != b["label"] and b["taught"] in b["name"].lower())
    return {"option": option, "n": len(pairs), "without": round(sum(a["right"] for a, _ in pairs) / len(pairs), 3),
            "with": round(sum(b["right"] for _, b in pairs) / len(pairs), 3), "helped": helped, "hurt": hurt,
            "nearestRight": round(sum(taught_right) / len(pairs), 3), "followedAWrongMatch": followed_wrong,
            "p50MsWithout": p([a["ms"] for a, _ in pairs], 0.5), "p50MsWith": p([b["ms"] for _, b in pairs], 0.5),
            "rows": rows}


# The names BioCLIP 2's text tower is asked about, per labelled set: the words a person would use.
ZERO_SHOT_NAMES = {
    "acevedo_et_al_2020": {"neutrophil": "neutrophil", "eosinophil": "eosinophil", "basophil": "basophil",
                           "lymphocyte": "lymphocyte", "monocyte": "monocyte", "immature granulocyte": "immature granulocyte",
                           "erythroblast": "erythroblast", "platelet": "platelet"},
    "hussain_et_al_2019": {"negative-nilm": "normal squamous epithelial cell",
                           "low-grade-lsil": "low-grade squamous intraepithelial lesion cell",
                           "high-grade-hsil": "high-grade squamous intraepithelial lesion cell",
                           "carcinoma-scc": "squamous cell carcinoma cell"},
}
TEMPLATES = {
    "name": ["{}"],
    "photo": ["a photo of a {}."],
    "microscope": ["a microscope image of a {}.", "a light micrograph of a {}.", "a {} under the microscope."],
    "specimen": ["a microscope image of a {name}, in {subject}.", "a {name} in {subject}, seen through a microscope."],
}


def zero_shot(items, vectors, model) -> dict:
    """Which of its specimen's names (ZERO_SHOT_NAMES) each picture's BioCLIP 2 vector is closest
    to, per prompt template (averaged over a template set's prompts), with no examples taught."""
    out = {}
    for tname, templates in TEMPLATES.items():
        res = {}
        for dataset, names in ZERO_SHOT_NAMES.items():
            labels = list(names)
            subject = VOCAB[dataset]["subject"]
            texts = []
            for label in labels:
                rows = model.text_vectors([t.format(names[label], name=names[label], subject=subject) for t in templates])
                mean = rows.mean(0)
                texts.append(mean / np.linalg.norm(mean))
            texts = np.stack(texts)
            idx = [i for i, item in enumerate(items) if item["dataset"] == dataset]
            sims = vectors[idx] @ texts.T
            order = np.argsort(-sims, 1)
            truth = [labels.index(items[i]["label"]) for i in idx]
            top1 = [int(order[r, 0]) == t for r, t in enumerate(truth)]
            top2 = [t in order[r, :2] for r, t in enumerate(truth)]
            margin = np.sort(sims, 1)
            picked = [labels[int(order[r, 0])] for r in range(len(idx))]
            res[dataset] = {"n": len(idx), "top1": round(sum(top1) / len(idx), 3), "top2": round(sum(top2) / len(idx), 3),
                            "chance": round(1 / len(labels), 3),
                            "marginP50": round(float(np.median(margin[:, -1] - margin[:, -2])), 4),
                            "picked": {lab: picked.count(lab) for lab in labels}}
        out[tname] = res
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=10, help="random teach/test splits")
    parser.add_argument("--naming", type=int, default=0, help="blood-cell pictures to name with and without the line (2 calls each)")
    parser.add_argument("--option", default="gemini-3.5-flash", help="the vision model naming clicks")
    parser.add_argument("--no-worker", action="store_true", help="skip the heavy worker round trip")
    parser.add_argument("--embed", default="dinov2-small", help="the embedding model (option EMBED)")
    parser.add_argument("--zeroshot", action="store_true", help="BioCLIP 2's text tower against the specimen's names")
    args = parser.parse_args()
    from gpu_worker.embed import embedder as make

    import torch

    items = load(None)
    out: dict = {"at": time.strftime("%Y-%m-%d %H:%M"), "embed": args.embed, "pictures": len(items), "teachPerClass": TEACH}
    images = crops(items, taught.MARGIN)
    out["latency"] = latency(images, args.embed)
    if not args.no_worker:
        out["worker"] = asyncio.run(worker_latency(images, args.embed))
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    embedder = make(args.embed, device)
    variants = {}
    for margin in (1.0, 1.5, 2.0):
        cut = images if margin == taught.MARGIN else crops(items, margin)
        for tokens in ("cls", "cls+mean") if args.embed == "dinov2-small" else ("image",):
            vectors = embedder.vectors(cut, tokens=tokens) if tokens != "image" else embedder.vectors(cut)
            for k in (1, 3):
                variants[f"margin {margin}, {tokens}, k={k}"] = {
                    "all": nearest_accuracy(items, vectors, args.seeds, k),
                    "blood": nearest_accuracy(items, vectors, args.seeds, k, sets={"acevedo_et_al_2020"}),
                    "pap": nearest_accuracy(items, vectors, args.seeds, k, sets={"hussain_et_al_2019"})}
    out["nearest"] = variants
    vectors = embedder.vectors(images)
    out["neverTaught"] = never_taught(items, vectors, taught.TOP_K)
    out["default"] = f"margin {taught.MARGIN}, {'cls' if args.embed == 'dinov2-small' else 'image'}, k={taught.TOP_K}"
    if args.zeroshot:
        out["zeroShot"] = zero_shot(items, vectors, embedder)
    if args.naming:
        out["naming"] = asyncio.run(naming(items, vectors, args.naming, args.option, model=args.embed))
    path = BENCH / "results" / f"taught-{args.embed}-{time.strftime('%Y%m%d-%H%M%S')}.json"
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
