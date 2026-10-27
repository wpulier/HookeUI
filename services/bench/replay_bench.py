"""The replay bench (session knowledge K4, contract 5 of docs/design/2026-10-07-session-knowledge.md; ADR 0013):
an implementation run again on exactly the captured inputs and prompts of past sessions, compared with what
happened. `make replay-bench BUNDLE=dir[,dir] IMPL=micro-sam-lm,edgetam-automask`.

- **Inputs:** session bundles (bench/bundle.py). Each population is replayed on its own saved view
  (`capture.jpg`, the camera's JPEG as the app received it), with its own prompt (the field, the crop the
  finder was given, the eyepiece disc), through the app's own path: `population.segment` and the runner,
  origin "replay", on a GPU worker process of its own (the real `GpuClient`), so what is replayed is what
  the app runs. Nothing it makes is kept as evidence, and its execution records stay in its result, never
  in the app's logs (so a replay is never an outcome).
- **Compared with what happened:**
  - **Where the user corrected the cells, the corrections are the truth**, read from the latest state
    (bench/export_corrections.py, finding 87): a cell removed as "not a cell" (does the replay find an
    instance there?) and cells merged into one (does it find the one, or the pieces again?). The recorded
    run agrees with none of them by construction: each was a correction of its output.
  - **Elsewhere it is agreement, never accuracy:** instances matched one to one with the recorded run's
    (revision 0, the model's own) at IoU >= 0.5, greedy by IoU; how many each found that the other did not;
    their mean IoU; the median area of each.
  - **Per subject**, as the execution recorded it (`labels.subject_key`), never pooled across subjects.
- **What winning governs:** only the recommendation line in its report (docs/bench/replay.md), which a
  person may cite when changing a default or an option's note. It never limits a choice: every available
  implementation stays usable, whether it has been replayed or not. A fine-tune is judged only on the
  sessions it held out (its model.json says which); the sessions it trained on are listed apart, never counted.

  cd services && MICROMIND_EDITION=research .venv/bin/python -m bench.replay_bench --bundle ~/bundles/leaf \\
      --impl cellpose-sam,micro-sam-lm,edgetam-automask
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.bundle import Recorded, load, recorded  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RESULTS = HERE / "results"
DOC = REPO / "docs" / "bench" / "replay.md"
THRESHOLD = 0.5  # IoU at which two instances are one cell (one to one, greedy by IoU)
# The recommendation rule, stated with every result. A candidate wins for a subject only on sessions it did not
# train on, with at least MIN_CORRECTIONS of the user's corrections to judge it by, when it agrees with at least
# WIN_AGREES of them (the recorded run agreed with none) and still finds at least WIN_KEEPS of the cells nobody
# corrected (so it did not win by finding less).
MIN_CORRECTIONS = 20
WIN_AGREES = 0.5
WIN_KEEPS = 0.9


# --- comparing instance sets ------------------------------------------------------------------------
def instance(mask) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """A mask at full capture resolution with its box (x0, y0, x1, y1), exclusive of x1/y1."""
    from orchestrator.evidence import _full_res

    bits = _full_res(mask)
    x0, y0 = int(mask.x0), int(mask.y0)
    return bits, (x0, y0, x0 + bits.shape[1], y0 + bits.shape[0])


def iou(a, b) -> float:
    (abits, (ax0, ay0, ax1, ay1)), (bbits, (bx0, by0, bx1, by1)) = a, b
    x0, y0, x1, y1 = max(ax0, bx0), max(ay0, by0), min(ax1, bx1), min(ay1, by1)
    if x0 >= x1 or y0 >= y1:
        return 0.0
    inter = int(np.count_nonzero(abits[y0 - ay0:y1 - ay0, x0 - ax0:x1 - ax0] & bbits[y0 - by0:y1 - by0, x0 - bx0:x1 - bx0]))
    if not inter:
        return 0.0
    return inter / float(int(abits.sum()) + int(bbits.sum()) - inter)


def match(a: dict, b: dict, threshold: float = THRESHOLD) -> tuple[list[tuple[str, str, float]], list[str], list[str]]:
    """One-to-one matches between two instance sets ({id: instance}), greedy by IoU, at IoU >= threshold:
    (pairs, ids only in a, ids only in b)."""
    pairs = []
    for ia, sa in a.items():
        for ib, sb in b.items():
            v = iou(sa, sb)
            if v >= threshold:
                pairs.append((v, ia, ib))
    pairs.sort(key=lambda p: -p[0])
    used_a, used_b, kept = set(), set(), []
    for v, ia, ib in pairs:
        if ia in used_a or ib in used_b:
            continue
        used_a.add(ia)
        used_b.add(ib)
        kept.append((ia, ib, round(v, 4)))
    return kept, [i for i in a if i not in used_a], [i for i in b if i not in used_b]


def agreement(recorded_set: dict, replayed_set: dict) -> dict:
    """Agreement between two instance sets of one view: matched one to one at IoU >= THRESHOLD, what each found
    that the other did not, the matches' mean IoU, and the share of instances matched (2 x matched / both).
    Agreement with what was recorded, never accuracy."""
    pairs, only_a, only_b = match(recorded_set, replayed_set)
    n_a, n_b = len(recorded_set), len(replayed_set)
    areas = lambda s: [int(v[0].sum()) for v in s.values()]  # noqa: E731
    return {"recorded": n_a, "replayed": n_b, "matched": len(pairs), "onlyRecorded": len(only_a), "onlyReplayed": len(only_b),
            "meanIoU": round(float(np.mean([p[2] for p in pairs])), 3) if pairs else None,
            "agreement": round(2 * len(pairs) / (n_a + n_b), 3) if n_a + n_b else None,
            "medianAreaRecorded": float(np.median(areas(recorded_set))) if n_a else None,
            "medianAreaReplayed": float(np.median(areas(replayed_set))) if n_b else None,
            "_pairs": pairs}


def truth(rec: Recorded) -> list[dict]:
    """The user's corrections of this population, as they stand now (finding 87: the latest state): each cell
    removed as not a cell, and each merge (one cell, from its pieces). An undone correction is no truth."""
    from bench.export_corrections import segmentation_rows

    # by day and id: population and member ids start again each day, so another day's e1 is another population
    rows = [r for r in segmentation_rows(rec.folder.parent.parent) if r["population"] == rec.id and r["day"] == rec.day]
    current = rec.current()
    items, seen = [], set()
    for r in rows:
        for mid, m in r["members"].items():
            if current.get(mid) == "deleted" and mid not in seen and mid in rec.masks:
                seen.add(mid)
                items.append({"kind": "not-a-cell", "member": mid, "revision": r["revision"], "words": r.get("userWords")})
        for added in r["added"]:
            mid = added["id"]
            if current.get(mid) not in ("deleted", "merged") and mid not in seen and mid in rec.masks:
                seen.add(mid)
                items.append({"kind": "one-cell", "member": mid, "parts": [p for p in (added.get("mergedFrom") or []) if p in rec.masks],
                              "revision": r["revision"], "words": r.get("userWords")})
    return items


def judge(items: list[dict], masks: dict, replayed_set: dict) -> dict:
    """How a set of instances stands against the user's corrections: for a cell removed as not a cell, it agrees
    when it finds no instance there (none at IoU >= THRESHOLD) and repeats it otherwise; for cells merged into
    one, it repeats the correction's cause when two or more of its instances are the pieces again (each at IoU
    >= THRESHOLD with a piece), agrees when it finds the one (IoU >= THRESHOLD with the merged cell), and differs
    otherwise. Pieces found again come first: a big piece alone can match the merged cell."""
    verdicts = []
    for item in items:
        target = instance(masks[item["member"]])
        hits = [i for i, s in replayed_set.items() if iou(target, s) >= THRESHOLD]
        if item["kind"] == "not-a-cell":
            verdict = "repeats" if hits else "agrees"
        else:
            pieces = {p: instance(masks[p]) for p in item["parts"]}
            again = {i for p, piece in pieces.items() for i, s in replayed_set.items() if iou(piece, s) >= THRESHOLD}
            verdict = "repeats" if len(again) >= 2 else ("agrees" if hits else "differs")
        verdicts.append({**item, "verdict": verdict})
    count = lambda v: sum(1 for x in verdicts if x["verdict"] == v)  # noqa: E731
    return {"judged": len(verdicts), "agreed": count("agrees"), "repeated": count("repeats"), "differs": count("differs"),
            "items": verdicts}


def kept_share(rec: Recorded, items: list[dict], replayed_set: dict, recorded_set: dict) -> dict:
    """Of the cells the model found that nobody corrected, how many the replay finds too (IoU >= THRESHOLD)."""
    touched = {i["member"] for i in items} | {p for i in items for p in i.get("parts", [])}
    untouched = {k: v for k, v in recorded_set.items() if k not in touched}
    pairs, _, _ = match(untouched, replayed_set)
    return {"n": len(untouched), "found": len(pairs), "share": round(len(pairs) / len(untouched), 3) if untouched else None}


# --- replaying ---------------------------------------------------------------------------------------
class ReplayHub:
    """A hub for replays: the app's runner, evidence store and capture store over a GPU worker process of the
    bench's own; no frames, no scene to draw on, nothing saved, and its runner's records kept by the bench."""

    def __init__(self, gpu_heavy) -> None:
        from orchestrator.capture import Captures
        from orchestrator.evidence import Evidence
        from orchestrator.operations import Runner
        from orchestrator.options import Settings
        from orchestrator.scene import Scene

        self.settings = Settings()
        self.scene = Scene()
        self.gpu = None
        self.gpu_heavy = gpu_heavy
        self.latest_info = None
        self.execution_day = None
        self.captures = Captures(self)
        self.evidence = Evidence(self, None)  # nothing a replay makes is kept as evidence
        self.records: list[dict] = []
        self.runner = Runner(self, log=self.records.append)


def capture_of(hub, rec: Recorded):
    """The population's saved view as the app takes a view read back: its JPEG and facts (an id and epoch of
    their own, so nothing places it on a live view), its eyepiece disc and its scale."""
    from orchestrator.capture import captures
    from orchestrator.fov import FieldOfView

    view = dict(rec.manifest.get("view") or {})
    disc = view.get("disc")
    info = SimpleNamespace(id=int(rec.manifest.get("capture") or 0), width=int(view.get("width") or 0),
                           height=int(view.get("height") or 0), epoch=view.get("epoch"), dx=float(view.get("dx") or 0.0),
                           dy=float(view.get("dy") or 0.0), fov=FieldOfView(disc[0], disc[1], disc[2], 1.0) if disc else None,
                           disc=float(view.get("discPx") or 0.0), seq=view.get("seq"), boot=view.get("boot"),
                           wall_at=float(view.get("at") or 0.0), tracked=False)
    return captures(hub).read_back(info, rec.jpeg(), rec.manifest.get("scaleId"), f"replay:{view.get('run')}")


async def replay_one(hub, rec: Recorded, impl: str, policy: str = "raw") -> dict:
    """Run `impl` on the population's own view and prompt, through the app's path. Returns its members' masks,
    its execution record and its time, or the reason it could not run."""
    from orchestrator.brain.registry import ToolFailed
    from orchestrator.population import segment

    region = rec.manifest.get("region")
    hub.scene.context["specimen"] = rec.row.get("subject") or ""
    try:
        population = await segment(hub, capture=capture_of(hub, rec), choice=impl, policy=policy, origin="replay",
                                   region=tuple(region) if region else None)
    except (ToolFailed, Exception) as exc:  # said as it is: a replay that could not run is a result too
        return {"ok": False, "error": str(exc)[:300], "masks": {}}
    try:
        masks = {m.id: m.mask for m in population.live() if m.mask is not None}
        execution = population.made_by.get("execution")
        record = next((r for r in hub.records if r.get("kind") == "execution" and r.get("id") == execution), {})
        return {"ok": True, "masks": masks, "execution": {k: record.get(k) for k in ("id", "operation", "requested", "executed",
                                                                                      "policy", "origin", "attempts", "ms")},
                "version": (population.producer or {}).get("version"), "ms": record.get("ms")}
    finally:
        if population.hold is not None:  # never kept: its hold goes now, and it leaves the store
            population.hold.release()
            population.hold = None
        hub.evidence.items.pop(population.id, None)


def trained_on(impl: str, version: str | None) -> dict | None:
    """For fine-tuned weights, what the version a replay ran with trained on (its model.json, finetuned.py): its days,
    and its views by JPEG and bridge frame; None for an implementation that was never trained here. A version whose
    description cannot be read counts as having trained on everything (never judged on it)."""
    from orchestrator import finetuned
    from orchestrator.operations import OPERATIONS

    found = OPERATIONS["cells"].impl(impl)
    if found is None or not found.weights:
        return None
    entry = next((v for v in finetuned.versions(found.weights) if v["version"] == version and v["whole"]), None)
    if entry is None:
        return {"version": version, "unknown": True, "days": set(), "views": set(), "frames": set()}
    rows = entry.get("trainedOn") or []
    return {"version": version, "unknown": False, "basis": (entry.get("split") or {}).get("basis", "day"),
            "days": {t.get("day") for t in rows}, "views": {t.get("captureSha256") for t in rows if t.get("captureSha256")},
            "frames": {(t.get("boot"), t.get("seq")) for t in rows if t.get("boot") and t.get("seq") is not None}}


def was_trained_on(rec: Recorded, tuned: dict | None) -> bool:
    """Whether this population's day or its very view is one the fine-tune trained on."""
    if tuned is None:
        return False
    if tuned["unknown"]:
        return True
    return bool(rec.day in tuned["days"] or rec.sha256() in tuned["views"] or (rec.frame()[0] and rec.frame() in tuned["frames"]))


def weights_version(impl_version: str | None) -> str | None:
    """"cellpose cpsam, fine-tuned v2" -> "v2" (what operations._cells says a fine-tune's population was made by)."""
    last = (impl_version or "").rsplit(" ", 1)[-1]
    return last if last.startswith("v") and last[1:].isdigit() else None


def checked_weights(impl: str) -> str:
    """For fine-tuned weights, why the current version must not run ("" when it may): its file must match the
    SHA-256 its description gives (read in full, once per bench run)."""
    from orchestrator import finetuned
    from orchestrator.operations import OPERATIONS

    found = OPERATIONS["cells"].impl(impl)
    current = finetuned.current(found.weights) if found is not None and found.weights else None
    if current is not None and not finetuned.verify(current):
        return f"its weights ({current['version']}) do not match the checksum their description gives"
    return ""


def tile(rec: Recorded, masks: dict, label: str, width: int = 420) -> np.ndarray:
    """The population's view inside its crop, the recorded cells outlined in green and the replayed ones in magenta."""
    import cv2

    image = cv2.imdecode(np.frombuffer(rec.jpeg(), np.uint8), cv2.IMREAD_COLOR)
    for colour, shown in (((60, 220, 60), [rec.masks[m] for m in rec.model_members() if m in rec.masks]),
                          ((255, 60, 255), list(masks.values()))):
        for mask in shown:
            bits, (x0, y0, _, _) = instance(mask)
            contours, _ = cv2.findContours(bits.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(image, [c + (x0, y0) for c in contours], -1, colour, 1)
    x, y, w, h = rec.manifest.get("region") or (0, 0, image.shape[1], image.shape[0])
    crop = image[y:y + h, x:x + w]
    crop = cv2.resize(crop, (width, max(1, int(round(crop.shape[0] * width / crop.shape[1])))))
    cv2.putText(crop, label, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
    return crop


async def run(bundles: list[Path], impls: list[str], policy: str = "raw", gpu=None, limit: int | None = None,
              tiles: dict | None = None, hub: ReplayHub | None = None, beside_live: bool = False) -> dict:
    """Replay every whole population of `bundles` with each of `impls`; the result as written to results/. With
    `tiles`, each replay's picture is kept there by (population, implementation), for a contact sheet. Without a
    GPU client (or a hub), a GPU worker process of the bench's own is started and stopped. It waits for the app
    (bench/live.py): refused while the app runs, unless `beside_live`, which its result then says."""
    from bench import live
    from orchestrator.operations import OPERATIONS, availability

    refusal = live.refuse("The replay bench", beside_live)
    if refusal:
        raise SystemExit(refusal)
    alongside = live.running() if beside_live else ""
    if hub is not None:
        gpu = hub.gpu_heavy
    owned = gpu is None
    if owned:
        from orchestrator.gpu_client import GpuClient

        gpu = GpuClient("micromind-gpu-replay", warm=False)
    hub = hub or ReplayHub(gpu)
    rows, first = [], {}
    pops = [rec for b in bundles for rec in recorded(b)]
    if limit:
        pops = pops[:limit]
    try:
        for impl in impls:
            spec = OPERATIONS["cells"].impl(impl)
            why = "no such cell finder" if spec is None else (availability(hub, spec) or checked_weights(impl))
            for rec in pops:
                base = {"bundle": rec.bundle.name, "day": rec.day, "population": rec.id, "subject": rec.subject,
                        "session": rec.session, "recordedImpl": rec.manifest.get("implementation"),
                        "recordedVersion": (rec.manifest.get("producer") or {}).get("version"), "impl": impl, "policy": policy}
                if why:
                    rows.append({**base, "ok": False, "error": f"not run: {why}"})
                    continue
                t0 = time.perf_counter()
                done = await replay_one(hub, rec, impl, policy)
                wall = (time.perf_counter() - t0) * 1000
                row = {**base, "ok": done["ok"], "firstCall": impl not in first, "wallMs": round(wall, 1)}
                first.setdefault(impl, True)
                if not done["ok"]:
                    rows.append({**row, "error": done["error"]})
                    continue
                recorded_set = {mid: instance(rec.masks[mid]) for mid in rec.model_members() if mid in rec.masks}
                replayed_set = {mid: instance(mask) for mid, mask in done["masks"].items()}
                items = truth(rec)
                compared = agreement(recorded_set, replayed_set)
                compared.pop("_pairs")
                row.update(compared, implVersion=done.get("version"), ms=done.get("ms"), execution=done.get("execution"),
                           corrections=judge(items, rec.masks, replayed_set),
                           recordedOnCorrections=judge(items, rec.masks, recorded_set),
                           uncorrected=kept_share(rec, items, replayed_set, recorded_set))
                # what made the recorded cells, run again (the same implementation and version): said apart, never a
                # candidate's figures
                row["self"] = row["recordedImpl"] == impl and row["implVersion"] == row["recordedVersion"]
                tuned = trained_on(impl, weights_version(done.get("version")))
                if tuned is not None:  # judged only on what the version that ran did not train on
                    row["trainedOn"] = was_trained_on(rec, tuned)
                    row["splitBasis"] = "unknown version" if tuned["unknown"] else tuned.get("basis")
                rows.append(row)
                if tiles is not None:
                    tiles[(f"{rec.bundle.name}/{rec.day}/{rec.id}", impl)] = tile(
                        rec, done["masks"], f"{rec.day} {rec.id} {impl}: {len(replayed_set)} (recorded {len(recorded_set)})")
                print(json.dumps({k: row.get(k) for k in ("population", "impl", "subject", "recorded", "replayed", "matched",
                                                          "agreement", "meanIoU", "ms")}), flush=True)
    finally:
        if owned:
            gpu.stop()
    return {"bench": "replay", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "threshold": THRESHOLD, "policy": policy,
            "besideLive": alongside or None,
            "rule": {"minCorrections": MIN_CORRECTIONS, "agrees": WIN_AGREES, "keeps": WIN_KEEPS, "heldOutBy": "day"},
            "bundles": [_bundle_line(b) for b in bundles], "implementations": impls, "runs": rows,
            "summary": summarise(rows)}


def _bundle_line(folder: Path) -> dict:
    d = load(folder)
    return {"id": d.get("id"), "window": d.get("window"), "populations": d["holds"].get("populations"),
            "correctedPopulations": d["holds"].get("correctedPopulations"), "lacks": d.get("lacks")}


# --- summarising ---------------------------------------------------------------------------------------
def figures(rows: list[dict]) -> dict:
    """What a set of replays found against what was recorded, pooled: counts, agreement, IoU, areas, times, the
    corrections judged and the share of uncorrected cells still found."""
    if not rows:
        return {}
    a, b, m = (sum(r[k] for r in rows) for k in ("recorded", "replayed", "matched"))
    ious = [r["meanIoU"] for r in rows if r.get("meanIoU") is not None]
    times = [r["ms"] for r in rows if r.get("ms") and not r.get("firstCall")]
    first = [r["ms"] for r in rows if r.get("ms") and r.get("firstCall")]
    areas = lambda key: [r[key] for r in rows if r.get(key) is not None]  # noqa: E731
    judged = sum(r["corrections"]["judged"] for r in rows)
    untouched = sum(r["uncorrected"]["n"] for r in rows)
    found = sum(r["uncorrected"]["found"] for r in rows)
    return {"n": len(rows), "recorded": a, "replayed": b, "matched": m, "onlyRecorded": a - m, "onlyReplayed": b - m,
            "agreement": round(2 * m / (a + b), 3) if a + b else None,
            "meanIoU": round(float(np.mean(ious)), 3) if ious else None,
            "msMedian": round(statistics.median(times), 1) if times else None,
            "msFirst": round(first[0], 1) if first else None,
            "areaRecorded": [min(areas("medianAreaRecorded")), max(areas("medianAreaRecorded"))] if areas("medianAreaRecorded") else None,
            "areaReplayed": [min(areas("medianAreaReplayed")), max(areas("medianAreaReplayed"))] if areas("medianAreaReplayed") else None,
            "corrections": {"judged": judged, "agreed": sum(r["corrections"]["agreed"] for r in rows),
                            "repeated": sum(r["corrections"]["repeated"] for r in rows),
                            "differs": sum(r["corrections"]["differs"] for r in rows)},
            "uncorrectedFound": round(found / untouched, 3) if untouched else None}


def summarise(rows: list[dict]) -> dict:
    """Per subject and implementation: as a candidate, what it found against what was recorded on populations another
    implementation (or version) made and it did not train on, and the user's corrections it agrees with; apart, its
    replays of what it made itself (how far it reproduces itself, never a candidate's figures); and the
    recommendation line."""
    out: dict[str, dict] = {}
    for row in rows:
        out.setdefault(row["subject"], {}).setdefault(row["impl"], []).append(row)
    summary = {}
    for subject, by_impl in out.items():
        summary[subject] = {}
        for impl, rs in by_impl.items():
            ran = [r for r in rs if r.get("ok")]
            itself = [r for r in ran if r.get("self")]
            counted = [r for r in ran if not r.get("self") and not r.get("trainedOn")]
            entry = {"populations": len(rs), "ran": len(ran), "trainedOn": [r["population"] for r in ran if r.get("trainedOn")],
                     "notRun": sorted({r.get("error") for r in rs if not r.get("ok")}), **figures(counted)}
            if itself:
                entry["itself"] = figures(itself)  # said apart: what made the recorded cells, run again
            entry["recommendation"] = recommendation(entry, impl, rs)
            summary[subject][impl] = entry
    return summary


def recommendation(entry: dict, impl: str, rows: list[dict]) -> str:
    """The one line winning governs: what the app may recommend for this subject, and why (never a limit)."""
    if not entry.get("ran"):
        return "no recommendation: it did not run here (" + "; ".join(entry.get("notRun") or ["unknown"]) + ")"
    if "recorded" not in entry:
        if entry.get("itself") and not entry.get("trainedOn"):
            return "the implementation that made the recorded cells, run again: how far it reproduces itself, not a candidate"
        return "no recommendation: every day here was one it trained on, or what it made itself"
    judged = entry["corrections"]["judged"]
    if judged < MIN_CORRECTIONS:
        return (f"no recommendation: {judged} of your corrections on days it did not train on (too few to say; "
                f"{MIN_CORRECTIONS} needed); agreement with the recorded cells is not accuracy")
    agrees = entry["corrections"]["agreed"] / judged
    keeps = entry.get("uncorrectedFound") or 0.0
    words = (f"agrees with {entry['corrections']['agreed']} of your {judged} corrections (the recorded run: none) and finds "
             f"{keeps:.0%} of the cells nobody corrected")
    if agrees >= WIN_AGREES and keeps >= WIN_KEEPS:
        return f"wins on days it did not train on: {words}; a person may make it the recommended default"
    return f"does not win: {words} (to win: at least {WIN_AGREES:.0%} and {WIN_KEEPS:.0%})"


# --- the report --------------------------------------------------------------------------------------
def report(result: dict) -> str:
    from orchestrator.operations import label_of

    alongside = result.get("besideLive")
    lines = ["# The replay bench (session knowledge K4)", "",
             "`bench/replay_bench.py` (`make replay-bench`) runs a cell finder again on the views and prompts of past",
             "sessions (session bundles, `make bundle`; docs/datasets.md) through the app's own path (`population.segment`,",
             "the runner, a GPU worker process), and compares what it finds with what was recorded. Written by the bench;",
             f"result in `services/bench/results/{result.get('out', 'replay-bench.json')}`.", "",
             f"Run {result['at']}, policy `{result['policy']}`. Instances are the same cell at IoU >= {result['threshold']}"
             " (one to one, greedy by IoU). "
             + (f"**Run beside the app:** {alongside}. Its times are not on a green meter and mean little."
                if alongside else "Run with the app stopped."), "",
             "**Agreement is not accuracy.** Where nobody corrected the cells, the bench says how far two finders agree,",
             "not which is right. The user's corrections are the only truth here: a cell removed as not a cell, cells",
             "merged into one (read from the latest state; an undone correction is none). The recorded run agrees with",
             "none of them by construction.", "",
             "**Itself, apart.** A finder run on what it made itself (the same implementation and version) says how far it",
             "reproduces itself, in its own row; those replays never count in its figures as a candidate.", "",
             f"**What winning governs:** only the recommendation line below. A candidate wins for a subject when, on days "
             f"it did not train on (a session is a day; a fine-tune's held-out days, and never a view it trained on), at "
             f"least {result['rule']['minCorrections']} of your corrections judge it, it agrees with at least "
             f"{result['rule']['agrees']:.0%} of them and still finds at least {result['rule']['keeps']:.0%} of the cells "
             "nobody corrected. Nothing is held back from use for not being replayed, and a person changes a default.", "",
             "## Sessions", ""]
    for b in result["bundles"]:
        w = b.get("window") or {}
        lines.append(f"- `{b['id']}`: {w.get('from')} to {w.get('to')} ({w.get('of')}), {b['populations']} populations, "
                     f"{b['correctedPopulations']} corrected.")
        for lack in b.get("lacks") or []:
            lines.append(f"  - Lacks: {lack}")
    for subject, by_impl in result["summary"].items():
        lines += ["", f"## Subject: {subject}", "",
                  "| Implementation | Populations | Recorded | Replayed | Matched | Only recorded | Only replayed | Agreement | "
                  "Mean IoU | Median area, recorded / replayed (px²) | Corrections agreed | Time |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for impl, e in by_impl.items():
            if "recorded" in e:
                lines.append(_row(label_of(impl), f"{e['n']} of {e['populations']}", e))
            elif not e["ran"]:
                lines.append(f"| {label_of(impl)} | {e['populations']} | not run | | | | | | | | | |")
            if e.get("itself"):
                lines.append(_row(f"{label_of(impl)}, itself (reproducing what it made)", f"{e['itself']['n']}", e["itself"]))
            if e.get("trainedOn"):
                lines.append(f"| {label_of(impl)}, on days it trained on | {len(e['trainedOn'])} | not counted | | | | | | | | | |")
        lines.append("")
        for impl, e in by_impl.items():
            lines.append(f"- **{label_of(impl)}:** {e['recommendation']}.")
    lines += ["", "## Per population", "",
              "| Day | Population | Subject | Implementation | Recorded | Replayed | Matched | Agreement | Mean IoU | Time |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in result["runs"]:
        where = f"{r.get('day', '')} | {r['bundle']}/{r['population']}"
        if not r.get("ok"):
            lines.append(f"| {where} | {r['subject']} | {label_of(r['impl'])} | {r.get('error')} | | | | | |")
            continue
        when = f"{r['ms'] / 1000:.1f} s" + (" (first call: loads the model)" if r.get("firstCall") else "") if r.get("ms") else ""
        tag = " (itself)" if r.get("self") else " (trained on this day: not counted)" if r.get("trainedOn") else ""
        lines.append(f"| {where} | {r['subject']} | {label_of(r['impl'])}{tag} | {r['recorded']} | {r['replayed']} | "
                     f"{r['matched']} | {r['agreement']} | {_n(r['meanIoU'])} | {when} |")
    if result.get("sheet"):
        lines += ["", f"Pictures (kept here only, not in git): `services/bench/results/{result['sheet']}`, each view inside its "
                  "crop with the recorded cells in green and the replayed ones in magenta."]
    return "\n".join(lines) + "\n"


def _row(label: str, populations: str, e: dict) -> str:
    c = e["corrections"]
    return (f"| {label} | {populations} | {e['recorded']} | {e['replayed']} | {e['matched']} | {e['onlyRecorded']} | "
            f"{e['onlyReplayed']} | {e['agreement']} | {_n(e['meanIoU'])} | {_span(e.get('areaRecorded'))} / "
            f"{_span(e.get('areaReplayed'))} | {c['agreed']} of {c['judged']} | {_seconds(e)} |")


def _n(value) -> str:
    return "" if value is None else str(value)


def _span(pair) -> str:
    if not pair:
        return ""
    a, b = (int(round(v)) for v in pair)
    return f"{a:,}" if a == b else f"{a:,}-{b:,}"


def _seconds(entry: dict) -> str:
    if entry.get("msMedian") is not None:
        return f"{entry['msMedian'] / 1000:.1f} s"
    if entry.get("msFirst") is not None:
        return f"{entry['msFirst'] / 1000:.1f} s (first call: loads the model)"
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay cell finders on past sessions (session bundles).")
    parser.add_argument("--bundle", required=True, help="bundle folders, comma-separated (make bundle)")
    parser.add_argument("--impl", default="cellpose-sam,micro-sam-lm,edgetam-automask", help="cell finders, comma-separated")
    parser.add_argument("--policy", default="raw", choices=("raw", "route"))
    parser.add_argument("--limit", type=int, default=None, help="at most this many populations")
    parser.add_argument("--out", default="replay-bench.json")
    parser.add_argument("--no-doc", action="store_true", help="do not rewrite docs/bench/replay.md")
    parser.add_argument("--beside-live", action="store_true", help="run even while the app runs (it may slow its clicks)")
    args = parser.parse_args(argv)
    bundles = [Path(b).expanduser() for b in args.bundle.split(",") if b]
    tiles: dict = {}
    result = asyncio.run(run(bundles, [i for i in args.impl.split(",") if i], args.policy, limit=args.limit, tiles=tiles,
                             beside_live=args.beside_live))
    result["out"] = args.out
    RESULTS.mkdir(parents=True, exist_ok=True)
    if tiles:
        import cv2

        result["sheet"] = Path(args.out).with_suffix(".jpg").name
        rows = []
        for population in dict.fromkeys(k[0] for k in tiles):
            row = [tiles[k] for k in tiles if k[0] == population]
            height = max(t.shape[0] for t in row)
            rows.append(np.hstack([np.pad(t, ((0, height - t.shape[0]), (0, 0), (0, 0))) for t in row]))
        width = max(r.shape[1] for r in rows)
        cv2.imwrite(str(RESULTS / result["sheet"]), np.vstack([np.pad(r, ((0, 0), (0, width - r.shape[1]), (0, 0))) for r in rows]))
    (RESULTS / args.out).write_text(json.dumps(result, indent=1, default=str) + "\n")
    if not args.no_doc:
        DOC.write_text(report(result))
    print(json.dumps(result["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
