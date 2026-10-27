"""Dust on the optics (orchestrator/dust.py, roadmap W4-3): is it found, and does telling the models help?

Two parts:

  detect   (no cloud) plays recordings through the hub's own eyepiece and slide-motion trackers and
           the dust map, as the live app does, clean and with synthetic dust: soft dark spots fixed in
           the eyepiece (6-25% darker, 3-15 px), drawn on every frame before anything sees it. Scores
           the spots found against the ones drawn: found, false spots, how soon, and the cost.
  models   (cloud keys) takes still frames of a recording with visible synthetic dust and asks the
           app's prompts with DUST "off" (told nothing), "tell" (told where the spots are) and
           "filter" (told, and the app drops what lies on a spot): the running look's boxes on dust
           and on the labelled cells, and naming a click on a dust spot and on a labelled cell.

Only the June 14 recordings are real microscope footage (footage/README.md); the corpus tours are
labelled pictures toured inside a drawn eyepiece. Results: bench/results/dust-<time>.json and a
section appended to docs/bench/dust.md.

    cd services && .venv/bin/python -m bench.dust_bench detect
    cd services && .venv/bin/python -m bench.dust_bench models --frames 12
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from orchestrator import dust as dustmod
from orchestrator.fov import FieldOfView, FovTracker
from orchestrator.motion import MotionTracker

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parents[1]
DOCS = ROOT / "docs" / "bench" / "dust.md"


def _videos() -> list[Path]:
    """The real leaf recordings and the blood smear tour, from the assets store (assets/README.md)."""
    from orchestrator.assets import store

    s = store()
    return [s.real_file(r) for r in REAL] + [s.corpus_footage / t for t in TOURS]


REAL = ("2026-06-14-leaf-epidermis-3m43s", "2026-06-14-leaf-epidermis-23s")
TOURS = ("corpus-bccd.mp4",)


def synthetic_spots(seed: int, count: int = 6, depth=(0.06, 0.25), radius=(3.0, 15.0)) -> list[dict]:
    """Spots fixed in the eyepiece: (u, v) in disc radii, radius in camera px, depth as a share."""
    rng = np.random.default_rng(seed)
    spots = []
    while len(spots) < count:
        u, v = rng.uniform(-0.75, 0.75, 2)
        if np.hypot(u, v) > 0.75 or any(np.hypot(u - s["u"], v - s["v"]) < 0.15 for s in spots):
            continue
        spots.append({"u": float(u), "v": float(v), "radius": float(rng.uniform(*radius)),
                      "depth": float(rng.uniform(*depth))})
    return spots


def dust_layer(shape, fov: FieldOfView, spots: list[dict]) -> np.ndarray:
    """A multiplicative layer (1 = untouched): soft Gaussian dark spots at their places in the disc."""
    h, w = shape[:2]
    layer = np.ones((h, w), np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    for s in spots:
        cx, cy = fov.cx + s["u"] * fov.radius, fov.cy + s["v"] * fov.radius
        sigma = s["radius"] / 2
        layer *= 1.0 - s["depth"] * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma ** 2))
    return layer


def first_disc(video: Path, frames: int = 30) -> FieldOfView | None:
    cap = cv2.VideoCapture(str(video))
    tracker, fov = FovTracker(), None
    for _ in range(frames):
        ok, bgr = cap.read()
        if not ok:
            break
        fov = tracker.update(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)) or fov
    return fov


def play(video: str, spots: list[dict] | None, every_s: float = 5.0) -> dict:
    """The live app's path over a recording: disc, slide motion and the dust map, with `spots`
    drawn on every frame. Returns the spots found over time and the costs."""
    path = Path(video)
    disc = first_disc(path)
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    fov_tracker, motion = FovTracker(), MotionTracker()
    holder: dict[int, object] = {}
    dm = dustmod.DustMap(lambda i: holder.get(i))
    layer = None
    fov, i, last = None, 0, 0.0
    frame_us, estimate_ms, timeline = [], [], []
    while True:
        ok, bgr = cap.read()
        if not ok:
            break
        if spots:
            if layer is None:
                layer = dust_layer(bgr.shape, disc, spots)
            bgr = np.clip(bgr.astype(np.float32) * layer[..., None], 0, 255).astype(np.uint8)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        fov = fov_tracker.update(gray) or fov
        sample = motion.update(i, gray, fov)
        ok, jpeg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        holder.clear()
        holder[i] = SimpleNamespace(jpeg=jpeg.tobytes())
        info = SimpleNamespace(id=i, fov=fov, wall_at=i / fps, dx=sample.dx, dy=sample.dy, epoch=sample.epoch)
        t0 = time.perf_counter()
        dm.frame(info)
        frame_us.append((time.perf_counter() - t0) * 1e6)
        t = i / fps
        if t - last >= every_s and len(dm.kept) >= dustmod.MIN_VIEWS:
            last = t
            t0 = time.perf_counter()
            found = dm.estimate()
            estimate_ms.append((time.perf_counter() - t0) * 1000)
            timeline.append({"t": round(t, 1), "views": dm.views, "spots": [s.wire() for s in found], "disc": dm.wire()["disc"]})
        i += 1
    if len(dm.kept) >= dustmod.MIN_VIEWS:
        t0 = time.perf_counter()
        found = dm.estimate()
        estimate_ms.append((time.perf_counter() - t0) * 1000)
        timeline.append({"t": round(i / fps, 1), "views": dm.views, "spots": [s.wire() for s in found], "disc": dm.wire()["disc"]})
    return {"video": path.name, "frames": i, "seconds": round(i / fps, 1), "disc": None if disc is None else
            [round(disc.cx, 1), round(disc.cy, 1), round(disc.radius, 1)], "drawn": spots or [],
            "kept": len(dm.kept), "timeline": timeline,
            "frameUsP50": round(float(np.median(frame_us)), 1) if frame_us else None,
            "frameUsMax": round(float(np.max(frame_us)), 1) if frame_us else None,
            "estimateMsP50": round(float(np.median(estimate_ms)), 1) if estimate_ms else None}


def match(drawn: list[dict], drawn_disc: list[float], found: list[dict], found_disc: list[float] | None) -> tuple[int, int]:
    """(drawn spots found, found spots that are not drawn ones), compared in camera pixels: drawn spots
    are placed in the disc they were drawn in, found ones in the dust map's reference disc. A found
    spot matches a drawn one within the drawn spot's radius plus 6 px."""
    if not found or found_disc is None:
        return 0, len(found)
    place = lambda s, d: (d[0] + s["u"] * d[2], d[1] + s["v"] * d[2])  # noqa: E731
    used, hits = set(), 0
    for d in drawn:
        dx, dy = place(d, drawn_disc)
        best = min(((float(np.hypot(place(f, found_disc)[0] - dx, place(f, found_disc)[1] - dy)), j)
                    for j, f in enumerate(found) if j not in used), default=None)
        if best is not None and best[0] <= d["radius"] + 6:
            used.add(best[1])
            hits += 1
    return hits, len(found) - len(used)


VISIBLE_DEPTH = 0.12  # drawn spots at least this deep count for "how soon"


def score(run: dict) -> dict:
    disc = run["disc"] or [640.0, 360.0, 330.0]
    last = run["timeline"][-1] if run["timeline"] else {"spots": [], "disc": None}
    hits, false = match(run["drawn"], disc, last["spots"], last.get("disc"))
    deep = [d for d in run["drawn"] if d["depth"] >= VISIBLE_DEPTH]
    first = None  # footage seconds until every spot at least 12% deep was found
    for step in run["timeline"]:
        h, _ = match(deep, disc, step["spots"], step.get("disc"))
        if deep and h == len(deep):
            first = step["t"]
            break
    by_depth = {"6-12%": [0, 0], "12-18%": [0, 0], "18-25%": [0, 0]}
    for d in run["drawn"]:
        key = "6-12%" if d["depth"] < 0.12 else "12-18%" if d["depth"] < 0.18 else "18-25%"
        by_depth[key][1] += 1
        h, _ = match([d], disc, last["spots"], last.get("disc"))
        by_depth[key][0] += h
    return {"video": run["video"], "drawn": len(run["drawn"]), "found": hits, "false": false, "views": run["kept"],
            "firstS": first, "byDepth": by_depth, "frameUsP50": run["frameUsP50"], "frameUsMax": run["frameUsMax"],
            "estimateMsP50": run["estimateMsP50"]}


def detect(args) -> dict:
    videos = [v for v in _videos() if v.exists()]
    jobs = []
    for video in videos:
        jobs.append((str(video), None))
        for seed in range(args.seeds):
            jobs.append((str(video), synthetic_spots(seed)))
    with ProcessPoolExecutor(args.workers) as pool:
        runs = list(pool.map(play, *zip(*jobs)))
    scored = [score(r) for r in runs]
    for s in scored:
        print(s)
    return {"runs": runs, "scored": scored}


# --- models ---------------------------------------------------------------------------------

DUST_WORDS = re.compile(r"\b(dust|debris|smudge|speck|particle|artifact|artefact|dirt|lens|optic|bubble|contaminant)", re.I)
VISIBLE = {"depth": (0.2, 0.35), "radius": (6.0, 14.0)}  # dust a person would notice


def still_frames(video: Path, truth: dict | None, count: int, seed: int) -> list[tuple[np.ndarray, list]]:
    """`count` frames spread over the recording, with the labelled things in view (corpus truth)."""
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    rng = np.random.default_rng(seed)
    picks = sorted(rng.choice(np.arange(30, max(31, total - 30)), size=count, replace=False).tolist())
    out = []
    for n in picks:
        cap.set(cv2.CAP_PROP_POS_FRAMES, n)
        ok, bgr = cap.read()
        if not ok:
            continue
        objects = (truth["frames"][n]["objects"] if truth and n < len(truth["frames"]) else [])
        out.append((bgr, objects))
    return out


async def models(args) -> dict:
    from orchestrator.agent import identify_context, identify_images, identify_reading
    from orchestrator.coords import gemini_box_to_frame
    from orchestrator.keys import load_keys
    from orchestrator.marks import encode_crop, fov_crop
    from orchestrator.options import VISION
    from orchestrator.prompts import IDENTIFY
    from orchestrator.sight import sight_prompt
    from orchestrator.vision import make_vision

    load_keys()
    vision = make_vision(VISION[args.option].spec)
    from orchestrator.assets import store

    footage = store().footage  # --videos are paths under it, e.g. corpus/corpus-bccd.mp4
    gate = asyncio.Semaphore(args.concurrency)
    rows: list[dict] = []
    sources = []
    for name in args.videos.split(","):
        video = footage / name
        if not video.exists():
            continue
        truth_path = video.with_suffix(".truth.json")
        truth = json.loads(truth_path.read_text()) if truth_path.exists() else None
        sources.append((video, truth))

    async def one_frame(video: Path, truth, index: int, bgr: np.ndarray, objects: list) -> None:
        disc = first_disc(video)
        drawn = synthetic_spots(1000 + index, count=4, depth=VISIBLE["depth"], radius=VISIBLE["radius"])
        layer = dust_layer(bgr.shape, disc, drawn)
        image = np.clip(bgr.astype(np.float32) * layer[..., None], 0, 255).astype(np.uint8)
        # What the dust map reports for these (its measured form: centre and radius in disc radii).
        spots = [dustmod.Spot(d["u"], d["v"], d["radius"] / disc.radius, d["depth"], 20, 22) for d in drawn]
        crop = fov_crop(disc, image.shape[1], image.shape[0])
        jpeg, _ = encode_crop(image, crop, max_side=768)
        # Cells the corpus labels, not touched by any spot: a control for naming and pointing.
        def off_dust(box):
            for s in spots:
                sx0, sy0, sx1, sy1 = s.frame_box(disc)
                if sx1 >= box[0] - 4 and sx0 <= box[2] + 4 and sy1 >= box[1] - 4 and sy0 <= box[3] + 4:
                    return False
            return True
        cells = [o for o in objects if off_dust(o["box"]) and np.hypot((o["box"][0] + o["box"][2]) / 2 - disc.cx,
                                                                      (o["box"][1] + o["box"][3]) / 2 - disc.cy) < 0.8 * disc.radius]
        for arm in ("off", "tell", "filter"):
            told = dustmod.line(spots, disc, crop) if arm != "off" else ""
            async with gate:
                result = await vision.ask([jpeg], sight_prompt(truth["specimen"] if truth else "leaf epidermis", None,
                                                               "", told), max_output_tokens=900)
            things = []
            for t in (result.data or {}).get("things") or []:
                box = t.get("box_2d") if isinstance(t, dict) else None
                if isinstance(box, list) and len(box) == 4:
                    try:
                        fb = gemini_box_to_frame([float(v) for v in box], crop)
                    except (TypeError, ValueError):
                        continue
                    on = bool(dustmod.spots_on(spots, disc, fb))
                    if arm == "filter" and on:
                        continue  # the code filter: what lies on a spot is dropped
                    things.append({"name": str(t.get("name") or ""), "box": [round(v) for v in fb], "onDust": on,
                                   "dustWord": bool(DUST_WORDS.search(str(t.get("name") or "")))})
            rows.append({"kind": "sight", "arm": arm, "video": video.name, "frame": index, "ms": round(result.latency_ms),
                         "things": things, "error": result.error or None})
            # Naming a click on a dust spot (the outline the app would draw around it) and on a labelled cell.
            clicks = [("dust", s.frame_box(disc), None) for s in spots[:2]]
            clicks += [("cell", tuple(o["box"]), o["label"]) for o in cells[:2]]
            for what, box, label in clicks:
                x0, y0, x1, y1 = box
                if what == "dust":  # an outline about twice the spot, as a click on it would give
                    cx, cy, r = (x0 + x1) / 2, (y0 + y1) / 2, max(8.0, (x1 - x0))
                    x0, y0, x1, y1 = cx - r, cy - r, cx + r, cy + r
                polygon = [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]
                view, close = identify_images(image, polygon, crop, (x0, y0, x1, y1))
                told = dustmod.line(spots, disc, crop, (x0, y0, x1, y1)) if arm != "off" else ""
                context = identify_context(truth["specimen"] if truth else "leaf epidermis", "", "", "", "")
                context = "\n".join(p for p in (context, told) if p)
                async with gate:
                    res = await vision.ask([view, close], IDENTIFY.format(context=context))
                reading = identify_reading(res.data or {})
                text = " ".join([reading["label"], reading.get("specific") or "", str((res.data or {}).get("answer") or "")])
                rows.append({"kind": "identify", "arm": arm, "on": what, "truth": label, "video": video.name, "frame": index,
                             "name": reading["label"], "specific": reading["specific"], "status": reading["status"],
                             "dustWord": bool(DUST_WORDS.search(reading["label"] + " " + (reading.get("specific") or ""))),
                             "dustInAnswer": bool(DUST_WORDS.search(text)),
                             "ms": round(res.latency_ms), "error": res.error or None})

    jobs = []
    for video, truth in sources:
        for index, (bgr, objects) in enumerate(still_frames(video, truth, args.frames, seed=7)):
            jobs.append(one_frame(video, truth, index, bgr, objects))
    await asyncio.gather(*jobs)
    return {"rows": rows, "summary": summarise_models(rows)}


def summarise_models(rows: list[dict]) -> dict:
    out = {}
    for arm in ("off", "tell", "filter"):
        sight = [r for r in rows if r["kind"] == "sight" and r["arm"] == arm and not r["error"]]
        things = [t for r in sight for t in r["things"]]
        named = [r for r in rows if r["kind"] == "identify" and r["arm"] == arm and not r["error"]]
        on_dust = [r for r in named if r["on"] == "dust"]
        on_cell = [r for r in named if r["on"] == "cell"]
        cell_right = [r for r in on_cell if r["truth"] and _cell_right(r["truth"], r["name"] + " " + (r["specific"] or ""))]
        out[arm] = {
            "looks": len(sight),
            "things": len(things),
            "thingsOnDust": sum(t["onDust"] for t in things),
            "looksPointingAtDust": sum(any(t["onDust"] and not t["dustWord"] for t in r["things"]) for r in sight),
            "dustClicks": len(on_dust),
            "dustClicksCalledDust": sum(r["dustWord"] for r in on_dust),
            "dustClicksNamedAsSpecimen": sum(not r["dustWord"] for r in on_dust),
            "cellClicks": len(on_cell),
            "cellClicksCalledDust": sum(r["dustWord"] for r in on_cell),
            "cellClicksRight": len(cell_right),
            "p50Ms": int(np.median([r["ms"] for r in named + sight])) if named or sight else None,
        }
    return out


CELL_WORDS = {"RBC": ("red", "erythro"), "Platelets": ("platelet", "thrombo"),
              "WBC": ("white", "leuko", "leuco", "neutro", "lympho", "mono", "eosin", "baso", "granulo")}


def _cell_right(truth: str, said: str) -> bool:
    return any(w in said.lower() for w in CELL_WORDS.get(truth, (truth.lower(),)))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="part", required=True)
    d = sub.add_parser("detect")
    d.add_argument("--seeds", type=int, default=3)
    d.add_argument("--workers", type=int, default=4)
    m = sub.add_parser("models")
    m.add_argument("--option", default="gemini-3.5-flash")
    m.add_argument("--videos", default="corpus/corpus-bccd.mp4,real/2026-06-14-leaf-epidermis-3m43s.mp4")
    m.add_argument("--frames", type=int, default=12)
    m.add_argument("--concurrency", type=int, default=6)
    args = parser.parse_args()
    started = time.strftime("%Y%m%d-%H%M%S")
    result = detect(args) if args.part == "detect" else asyncio.run(models(args))
    out = BENCH / "results" / f"dust-{args.part}-{started}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"at": started, "part": args.part, **result}, indent=1))
    if args.part == "models":
        print(json.dumps(result["summary"], indent=1))
    print(f"wrote {out.relative_to(BENCH.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
