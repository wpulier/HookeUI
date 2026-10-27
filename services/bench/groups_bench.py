"""Groups by look against labelled kinds (session knowledge K1's bench): do the look groups the app makes
(enrichment.cluster: k-means over one picture model's vectors, the number of groups by silhouette) gather
cells of one kind?

Uses the naming bench's labelled pictures (bench/naming, `python -m bench.naming.fetch --yes`): the blood
smear set, 8 kinds of blood cell, 15 pictures each, each put in an eyepiece and cropped as the app crops a
member (taught.crop_for). Embedded in this process with each EMBED model that loads here.

Reported per model:
- the number of groups the silhouette chose, and its score;
- purity: the share of cells whose group's most common kind is their own (1.0: every group one kind);
- the adjusted Rand index against the kinds (0: chance, 1: the same partition);
- the same with k forced to 8 (the number of kinds), to separate the criterion from the vectors.

A group is a group: these numbers say how far a group can be read as a kind on this set, never that it is.

    PYTHONPATH=. python -m bench.groups_bench [--models dinov2-small,bioclip-2]
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np

from bench.naming import composite
from bench.naming.fetch import DATA
from bench.naming.run import load
from orchestrator import enrichment, taught

BENCH = Path(__file__).resolve().parent
BLOOD = "acevedo_et_al_2020"


def crops(items):
    import cv2

    out = []
    for i, item in enumerate(items):
        picture = cv2.imread(str(DATA / item["image"]))
        frame = composite.eyepiece(picture, seed=i)
        _, box = composite.object_outline(picture.shape[:2], item.get("bbox") if isinstance(item.get("bbox"), list) else None)
        out.append(taught.crop_for(frame, box))
    return out


def purity(groups: list[str], kinds: list[str]) -> float:
    by_group: dict[str, Counter] = {}
    for g, k in zip(groups, kinds):
        by_group.setdefault(g, Counter())[k] += 1
    return sum(c.most_common(1)[0][1] for c in by_group.values()) / len(kinds)


def adjusted_rand(a: list[str], b: list[str]) -> float:
    """The adjusted Rand index of two partitions (Hubert and Arabie), numpy only."""
    from math import comb

    pairs = Counter(zip(a, b))
    sum_cells = sum(comb(n, 2) for n in pairs.values())
    sum_a = sum(comb(n, 2) for n in Counter(a).values())
    sum_b = sum(comb(n, 2) for n in Counter(b).values())
    total = comb(len(a), 2)
    expected = sum_a * sum_b / total if total else 0.0
    best = (sum_a + sum_b) / 2
    return (sum_cells - expected) / (best - expected) if best != expected else 0.0


def judge(ids, vectors, kinds, k_min=enrichment.K_MIN, k_max=enrichment.K_MAX) -> dict:
    out = enrichment.cluster(ids, vectors, k_min=k_min, k_max=k_max)
    groups = [out["groups"][i] for i in ids]
    return {"k": out["k"], "silhouette": out["silhouette"], "purity": round(purity(groups, kinds), 3),
            "ari": round(adjusted_rand(groups, kinds), 3), "tried": out["tried"],
            "kindsPerGroup": {g: dict(Counter(k for gg, k in zip(groups, kinds) if gg == g).most_common(3))
                              for g in sorted(set(groups), key=lambda s: int(s[1:]))}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="dinov2-small,bioclip-2,dinov3-small")
    args = parser.parse_args()
    from gpu_worker.embed import embedder

    items = [i for i in load(None) if i["dataset"] == BLOOD]
    kinds = [i["label"] for i in items]
    images = crops(items)
    ids = [f"c{n}" for n in range(len(items))]
    report = {"set": f"{BLOOD}: {len(items)} cells, {len(set(kinds))} kinds", "at": time.strftime("%Y-%m-%d %H:%M"),
              "chance": {"purity": round(max(Counter(kinds).values()) / len(kinds), 3)}, "models": {}}
    for model in [m for m in args.models.split(",") if m]:
        try:
            t0 = time.perf_counter()
            make = embedder(model, "cpu")
            vectors = np.asarray(make.vectors(images), np.float32)
            ms = (time.perf_counter() - t0) * 1000 / len(images)
        except Exception as exc:  # gated weights, a package not installed: said, not hidden
            report["models"][model] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            print(f"{model}: not run ({type(exc).__name__})")
            continue
        chosen = judge(ids, vectors, kinds)
        forced = judge(ids, vectors, kinds, k_min=len(set(kinds)), k_max=len(set(kinds)))
        report["models"][model] = {"bySilhouette": chosen, "atKinds": forced, "msPerCrop": round(ms, 1)}
        print(f"{model}: silhouette chose k={chosen['k']} ({chosen['silhouette']:.2f}): purity {chosen['purity']:.2f}, "
              f"ARI {chosen['ari']:.2f}; at k=8: purity {forced['purity']:.2f}, ARI {forced['ari']:.2f}")
    out = BENCH / "results" / "groups.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    print(f"chance purity {report['chance']['purity']:.2f} -> {out}")


if __name__ == "__main__":
    main()
