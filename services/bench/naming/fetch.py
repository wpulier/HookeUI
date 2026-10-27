"""Fetch labelled pictures for the naming bench: a sample of Micro-Bench's brightfield sets
(blood cells, pollen, Pap smears), only rows whose licence allows reuse (CC-BY, CC-BY-SA,
CC0). Pictures go to the assets store's bench/naming/ (assets/README.md) with a manifest.

Micro-Bench (Lozano et al., NeurIPS 2024; huggingface.co/datasets/jnirschl/uBench) is 3.6 GB
in all; this takes only the sampled rows, through the Hugging Face dataset server, one picture
at a time (a few hundred pictures, tens of MB).

    uv run python -m bench.naming.fetch            # what it would fetch (rows, licences, size): no pictures
    uv run python -m bench.naming.fetch --yes      # fetch them
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)
DATA = store().bench("naming")
SERVER = "https://datasets-server.huggingface.co"
DATASET = "jnirschl/uBench"
# Labelled set -> pictures per class to sample.
SETS = {"acevedo_et_al_2020": 15, "icpr2020_pollen": 20, "hussain_et_al_2019": 15}
LICENCES = {"CC-BY-4.0", "CC-BY-SA-4.0", "CC0"}
SEED = 20261005


def _rows(client: httpx.Client, offset: int, length: int) -> dict:
    for attempt in range(5):
        time.sleep(0.6)  # the dataset server rate-limits; be gentle
        try:
            reply = client.get(f"{SERVER}/rows", params={"dataset": DATASET, "config": "default", "split": "test",
                                                          "offset": offset, "length": length}, timeout=120)
            reply.raise_for_status()
            return reply.json()
        except httpx.HTTPError as exc:
            if attempt == 4:
                raise
            limited = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429
            wait = float(exc.response.headers.get("retry-after", 0) or 0) if limited else 0
            time.sleep(max(wait, 20 * (attempt + 1) if limited else 3 * (attempt + 1)))
    raise RuntimeError("unreachable")


def locate(client: httpx.Client, wanted: set[str], step: int = 150, page: int = 100) -> dict[str, list[int]]:
    """Micro-Bench stores each labelled set as one block of rows: sample every `step`-th row
    (metadata only) to find the pages of `page` rows each wanted set may be on (a block of
    fewer rows than `step` can sit between two samples, so neighbours are searched too)."""
    total = _rows(client, 0, 1)["num_rows_total"]
    offsets = list(range(0, total, step))
    seen = {offset: str(_rows(client, offset, 1)["rows"][0]["row"]["dataset"] or "") for offset in offsets}
    pages: dict[str, list[int]] = {name: [] for name in wanted}
    for i, offset in enumerate(offsets):
        nxt = offsets[i + 1] if i + 1 < len(offsets) else total
        for name in wanted:
            if seen[offset] == name or seen.get(nxt) == name:
                pages[name] += [p for p in range(offset, nxt, page) if p not in pages[name]]
    return pages


def set_rows(client: httpx.Client, labelled_set: str, pages: list[int], step: int = 100) -> list[dict]:
    rows: dict[str, dict] = {}  # by image id: neighbouring pages overlap
    for offset in sorted(set(pages)):
        for r in _rows(client, offset, step)["rows"]:
            if r["row"]["dataset"] == labelled_set:
                rows[r["row"]["image_id"]] = r["row"]
    return list(rows.values())


def sample(rows: list[dict], per_class: int, rng: random.Random) -> list[dict]:
    by_class = defaultdict(list)
    for r in rows:
        if r.get("license") in LICENCES and str(r.get("synthetic")).lower() != "true":
            by_class[r["label_name"]].append(r)
    chosen = []
    for label in sorted(by_class):
        group = sorted(by_class[label], key=lambda r: r["image_id"])
        chosen += rng.sample(group, min(per_class, len(group)))
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="fetch the pictures (without it: only list what it would)")
    args = parser.parse_args()
    rng = random.Random(SEED)
    plan = []
    with httpx.Client(follow_redirects=True) as client:
        print("finding the labelled sets (metadata only)...", flush=True)
        where = locate(client, set(SETS))
        for labelled_set, per_class in SETS.items():
            rows = set_rows(client, labelled_set, where[labelled_set])
            chosen = sample(rows, per_class, rng)
            licences = sorted({r["license"] for r in rows})
            print(f"{labelled_set}: {len(rows)} rows (licences {', '.join(licences)}), "
                  f"sampling {len(chosen)} across {len({r['label_name'] for r in chosen})} classes")
            plan += [(labelled_set, r) for r in chosen]
        print(f"{len(plan)} pictures planned (about {len(plan) * 40 // 1000 + 1} MB)")
        if not args.yes:
            print("Nothing fetched: run again with --yes to fetch them.")
            return 0
        manifest = []
        for i, (labelled_set, r) in enumerate(plan):
            folder = DATA / labelled_set
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{r['image_id']}.jpg"
            if not path.exists():
                reply = client.get(r["image"]["src"], timeout=60)
                reply.raise_for_status()
                path.write_bytes(reply.content)
            manifest.append({"image": str(path.relative_to(DATA)), "dataset": labelled_set, "label": r["label_name"],
                             "stain": r.get("stain"), "micronsPerPixel": r.get("microns_per_pixel"),
                             "licence": r["license"], "source": f"{DATASET} {r['image_id']}", "pmid": r.get("pmid"),
                             "bbox": r.get("bbox")})
            if i % 25 == 0:
                print(f"  {i + 1}/{len(plan)}", flush=True)
        (DATA / "manifest.jsonl").write_text("".join(json.dumps(m) + "\n" for m in manifest))
        print(f"fetched {len(manifest)} pictures -> {DATA.relative_to(DATA.parents[2])}/manifest.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
