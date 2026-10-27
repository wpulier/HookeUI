"""Dry run of the H14 scoring and analysis on synthetic sessions (GO lane-qualification 2). No person takes part.

It builds small synthetic packages (bench.h14_fixtures), simulates 250 completers (Amendment 3) on the frozen sequences and
variants (h14-study-design.md §5), turns each simulated attempt into an observation (a correct one taken from the
package's references, or a realistically wrong one), scores every observation with the real oracles
(bench.h14_scoring), and runs the frozen analysis (bench.h14_analysis). Scenarios check that the rules react as
the design says:

- equal: both apps alike, so most tasks should pass;
- hooke_worse_T7: Hooke's T7 completion is 20 points lower, so T7 must not pass;
- silent_wrong_T8: one Hooke T8 attempt is an app-caused wrong result, so T8 must fail (H1b);
- repair_T5: one Hooke T5 attempt needed developer repair, so T5 must fail (H1a).

Simulated behaviour is not evidence about either app. The output says "synthetic dry run" everywhere it goes.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from bench import h14_analysis, h14_fixtures, h14_scoring, h14_tasks

SEQUENCES = ("S1", "S2", "S3", "S4")
SESSION_ORDER = {"S1": (("napari", "hooke"), ("hooke", "napari")), "S2": (("napari", "hooke"), ("hooke", "napari")),
                 "S3": (("hooke", "napari"), ("napari", "hooke")), "S4": (("hooke", "napari"), ("napari", "hooke"))}
FIRST_VARIANT = {"S1": "D1", "S2": "D2", "S3": "D1", "S4": "D2"}


def allocation(n: int, seed: int = 20261020) -> list[str]:
    """Sequences in randomised blocks of 4 (§2.2), from a fixed seed (§5: the reviewer holds the real list)."""
    rng = np.random.default_rng(seed)
    out = []
    while len(out) < n:
        out.extend(rng.permutation(SEQUENCES).tolist())
    return out[:n]


def slot(sequence: str, task: str, app: str) -> tuple[int, str]:
    """(period within the session, variant) for one app's attempt at one task (§5)."""
    session = h14_tasks.BY_ID[task].session
    order = SESSION_ORDER[sequence][session - 1]
    period = order.index(app) + 1
    first, other = FIRST_VARIANT[sequence], ("D2" if FIRST_VARIANT[sequence] == "D1" else "D1")
    return period, first if period == 1 else other


# --- observations ---------------------------------------------------------------------------------------------

def _p(pkg: Path) -> dict:
    return json.loads((pkg / "params.json").read_text())


def _r(pkg: Path, name: str) -> np.ndarray:
    return np.load(pkg / "reference" / f"{name}.npy")


def ideal(task: str, pkg: Path) -> dict:
    """The observation of an attempt that did exactly what the card asks."""
    p = _p(pkg)
    e = p.get("expect", {})
    if task == "T0":
        return {"installed": True, "receipt": True, "labels": _r(pkg, "labels"), "hosts_seen": ["files.example"],
                "hosts_declared": ["files.example"]}
    if task == "T1":
        return {"value": e["value"], "position_um": e["position_um"], "displayed_axes": e["displayed_axes"]}
    if task == "T2":
        return {"channels": e["channels"], "slab": e["slab"]}
    if task == "T3":
        return {"render": e["render"], "threshold": e["threshold"], "plane_depth": e["plane_depth"],
                "picked": e["picked"], "picked_in_3d": True}
    if task == "T4":
        return {k: e[k] for k in ("parent", "vertex_value", "arrow_length", "out_of_slice", "point_size_factor", "grid")}
    if task in ("T5", "T13"):
        return {"value": e["value"], "level": 0, "t7_output_unchanged": True}
    if task == "T6":
        return {"points": e["centres"], "rectangle": e["rectangle"], "polygon": e["polygon"], "polygon_behind_rectangle": True}
    if task == "T7":
        truth = _r(pkg, "truth")
        out = _r(pkg, "start").copy()
        m = p["marks"]
        merge = _r(pkg, "merge_region").astype(bool)
        out[merge] = 0
        a, b = m["merged_ids"]
        out[truth == a] = a
        out[truth == b] = int(out.max()) + 1
        out[_r(pkg, "false_region").astype(bool)] = 0
        out[_r(pkg, "hole_region").astype(bool)] = m["hole_id"]
        return {"labels": out}
    if task == "T8":
        return {"labels": _r(pkg, "labels")}
    if task == "T9":
        return {"rows": e["rows"]}
    if task == "T10":
        refs = sorted((pkg / "reference" / "keyframes").glob("*.npy"))
        return {"frames": e["frames"], "keyframes": e["keyframes"],
                "frame_images": {i: np.load(r) for i, r in zip(e["keyframe_index"], refs)}}
    if task == "T11":
        return {"saved": _r(pkg, "after-e1"), "final": _r(pkg, "after-e2")}
    if task == "T12":
        return {"max_projection": _r(pkg, "max_projection"), "labels": _r(pkg, "labels_changed"),
                "answer": e["answer"], "kernel_alive": True}
    raise KeyError(task)


def wrong(task: str, pkg: Path, rng: np.random.Generator) -> dict:
    """A realistically wrong observation: what a participant leaves when they finish but get it wrong."""
    o = ideal(task, pkg)
    if task in ("T1", "T5", "T13"):
        o["value"] = int(o["value"]) + 1
    elif task == "T2":
        name = next(n for n, c in o["channels"].items() if c["visible"])
        o["channels"] = {**o["channels"], name: {**o["channels"][name], "colormap": "grey"}}
    elif task == "T3":
        o["picked_in_3d"] = False
    elif task == "T4":
        o["parent"] = -1
    elif task == "T6":
        o["points"] = o["points"][:-1]
    elif task in ("T7", "T8"):
        lab = o["labels"].copy()
        lab[lab == lab.max()] = 0
        o["labels"] = lab
    elif task == "T9":
        o["rows"] = o["rows"][:-1]
    elif task == "T10":
        o["frames"] = o["frames"] - 1
    elif task == "T11":
        o["final"] = o["saved"]
    elif task == "T12":
        o["kernel_alive"] = False
    elif task == "T0":
        o["hosts_seen"] = o["hosts_seen"] + ["undeclared.example"]
    return o


# --- sessions -------------------------------------------------------------------------------------------------

SCENARIOS = ("equal", "hooke_worse_T7", "silent_wrong_T8", "repair_T5")


def simulate(packages: Path, scenario: str = "equal", n: int = 250, seed: int = 7) -> tuple[list[dict], dict]:
    rng = np.random.default_rng(seed)
    seqs = allocation(n)
    cache: dict[tuple[str, str, str], bool] = {}
    scored: dict[str, dict] = {}

    def correct(task, variant, kind):
        key = (task, variant, kind)
        if key not in cache:
            pkg = packages / task / variant
            obs = ideal(task, pkg) if kind == "ideal" else wrong(task, pkg, np.random.default_rng(1))
            res = h14_scoring.score(pkg, obs)
            cache[key] = res["correct"]
            scored[f"{task}/{variant}/{kind}"] = res["checks"]
        return cache[key]

    attempts = []
    for i in range(n):
        pid, seq = f"P{i + 1:03d}", seqs[i]
        stratum = "naive" if i % 5 < 3 else "regular"
        site = f"site-{i % 3 + 1}"
        skill = rng.normal(0, 0.6)
        for task in h14_tasks.TASKS:
            apps = ("napari", "hooke") if task.comparison else ("hooke",)
            for app in apps:
                period, variant = slot(seq, task.id, app) if task.comparison else (1, "D1")
                cap = task.cap_s or h14_tasks.U_CAP_S
                p_ok = 0.90
                if scenario == "hooke_worse_T7" and task.id == "T7" and app == "hooke":
                    p_ok = 0.70
                p_ok = 1 / (1 + math.exp(-(math.log(p_ok / (1 - p_ok)) + skill)))
                ok = rng.random() < p_ok
                level = 0 if ok else int(rng.choice([0, 2, 3], p=[0.4, 0.4, 0.2]))
                l1 = int(rng.integers(0, 3)) if ok else 0
                time_s = float(np.clip(cap * math.exp(rng.normal(-1.1 - 0.2 * skill, 0.45)), 20, cap))
                done = ok or level != 0 or rng.random() < 0.5
                app_wrong = False
                if task.comparison and task.id != "T14":
                    is_correct = correct(task.id, variant, "ideal" if ok else "wrong")
                else:
                    is_correct = bool(ok)
                    if task.id.startswith("U"):
                        crit = {k: ok for k in h14_scoring.U_CRITERIA[task.id]}
                        is_correct = h14_scoring.score(Path("."), {"criteria": crit}, task.id)["correct"]
                if scenario == "silent_wrong_T8" and task.id == "T8" and app == "hooke" and i == 11:
                    app_wrong, is_correct, ok = True, False, False
                if scenario == "repair_T5" and task.id == "T5" and app == "hooke" and i == 23:
                    level = 4
                attempts.append({"participant": pid, "sequence": seq, "stratum": stratum, "site": site,
                                 "task": task.id, "app": app, "variant": variant, "period": period,
                                 "time_s": round(time_s, 1), "cap_s": float(cap), "done": bool(done),
                                 "correct": bool(is_correct), "max_level": level, "l1_count": l1,
                                 "lost": False, "app_caused_wrong": app_wrong})
    return attempts, scored


def run(out: Path, scenarios=SCENARIOS, n: int = 250) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    packages = out / "packages"
    if not (packages / "PACKAGE.sha256").exists():
        h14_fixtures.build(packages, scale="small")
    summary = {"note": "synthetic dry run: simulated behaviour, not evidence about napari or Hooke", "scenarios": {}}
    for scenario in scenarios:
        attempts, scored = simulate(packages, scenario, n)
        path = out / f"attempts-{scenario}.jsonl"
        path.write_text("".join(json.dumps(a, sort_keys=True) + "\n" for a in attempts))
        result = h14_analysis.analyse(attempts, required_completers=n)
        (out / f"analysis-{scenario}.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
        summary["scenarios"][scenario] = {
            "verdicts": {t: r["verdict"] for t, r in result["tasks"].items()},
            "oracle_checks": scored,
        }
    (out / "dry-run-summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="H14 scoring and analysis dry run on synthetic sessions")
    ap.add_argument("out", type=Path)
    ap.add_argument("--n", type=int, default=250)
    args = ap.parse_args(argv)
    summary = run(args.out, n=args.n)
    for name, s in summary["scenarios"].items():
        counts = {v: list(s["verdicts"].values()).count(v) for v in ("pass", "inconclusive", "fail")}
        bad = {t: v for t, v in s["verdicts"].items() if v != "pass"}
        print(f"{name}: {counts}  not passing: {bad}")


if __name__ == "__main__":
    main()
