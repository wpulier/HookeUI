"""Oracles for the H14 tasks (qualification/h14-study-design.md §4.1 "Oracle and tolerance", §4.3, §7.3).

`score(package_dir, observed)` returns {"correct": bool, "checks": {name: bool}, "details": {...}}. `observed` is the
normalised observation of one attempt: the values the participant reported and the state or files the app saved,
read after the participant says "done" (never during the attempt). Its fields per task are in OBSERVED. The two app
adapters that produce it (napari: bench.h14_state_napari; Hooke: from the saved Project) are separate, so the same
oracle scores both apps.

Every check is computed here from the package's own references, not taken from the app. Scoring is automated and
runs before anyone looks at summary results (§7.3).
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import cv2
import numpy as np

OBSERVED = {
    "T0": "installed, receipt (bools); labels (array); hosts_seen, hosts_declared (lists)",
    "T1": "value (int); position_um {z,y,x}; displayed_axes (list)",
    "T2": "channels {name: {limits [lo,hi], colormap, visible}}; slab {thickness, centre, mode}",
    "T3": "render; threshold; plane_depth; picked (int); picked_in_3d (bool)",
    "T4": "parent; vertex_value; arrow_length; out_of_slice; point_size_factor; grid",
    "T5": "value (int); level (int, the resolution level read)",
    "T6": "points [[y,x]...]; rectangle [[y,x]x4]; polygon [[y,x]...]; polygon_behind_rectangle (bool)",
    "T7": "labels (array)",
    "T8": "labels (array)",
    "T9": "csv (path) or rows [{label, area_um2, mean_intensity}]",
    "T10": "frames (int); keyframes (int); frame_images {index: array} for the keyframe indices",
    "T11": "saved (array, copied after quit, before reopening); final (array)",
    "T12": "max_projection (array); labels (array); answer; kernel_alive (bool)",
    "T13": "value (int); t7_output_unchanged (bool)",
    "U*": "criteria {name: bool} from the moderator's checklist for that task (materials/moderator-script.md)",
}
U_CRITERIA = {
    "U1": ("both_versions_kept", "chosen_version_accepted", "nothing_else_changed"),
    "U2": ("refusal_understood", "valid_input_used"),
    "U3": ("outputs_match_reference", "rollback_restores_earlier_generation"),
    "U4": ("successful_outputs_kept", "only_failed_items_rerun"),
    "U5": ("candidate_reviewed", "previous_generation_active", "saved_work_unchanged"),
    "U6": ("package_prepared_through_offered_path", "data_reopen_exactly", "nothing_deleted"),
    "U7": ("status_reported_matches_record", "missing_requirement_matches_record"),
}


def _params(d: Path) -> dict:
    return json.loads((d / "params.json").read_text())


def _ref(d: Path, name: str) -> np.ndarray:
    return np.load(d / "reference" / f"{name}.npy")


def _result(checks: dict, **details) -> dict:
    checks = {k: bool(v) for k, v in checks.items()}
    details = json.loads(json.dumps(details, default=lambda v: v.item() if hasattr(v, "item") else str(v)))
    return {"correct": all(checks.values()), "checks": checks, "details": details}


# --- shared measures ------------------------------------------------------------------------------------------

def iou_matrix(truth: np.ndarray, pred: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """IoU between every truth object and every predicted object (background 0 excluded)."""
    t_ids = np.unique(truth[truth > 0])
    p_ids = np.unique(pred[pred > 0])
    if len(t_ids) == 0 or len(p_ids) == 0:
        return np.zeros((len(t_ids), len(p_ids))), t_ids, p_ids
    ti = np.searchsorted(t_ids, truth.ravel())
    pi = np.searchsorted(p_ids, pred.ravel())
    both = (truth.ravel() > 0) & (pred.ravel() > 0)
    inter = np.zeros((len(t_ids), len(p_ids)))
    np.add.at(inter, (ti[both], pi[both]), 1)
    t_area = np.array([(truth == i).sum() for i in t_ids])[:, None]
    p_area = np.array([(pred == i).sum() for i in p_ids])[None, :]
    return inter / (t_area + p_area - inter), t_ids, p_ids


def object_f1(truth: np.ndarray, pred: np.ndarray, threshold: float = 0.5) -> float:
    """Object-level F1 at an IoU threshold; above 0.5 a match is unique, so counting suffices."""
    iou, t_ids, p_ids = iou_matrix(truth, pred)
    if len(t_ids) == 0 and len(p_ids) == 0:
        return 1.0
    tp = int(np.sum(iou.max(axis=1) > threshold)) if iou.size else 0
    fp, fn = len(p_ids) - tp, len(t_ids) - tp
    return 2 * tp / (2 * tp + fp + fn)


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Mean SSIM (Wang et al. 2004; Gaussian window sigma 1.5, K1 0.01, K2 0.03) on 8-bit greyscale."""
    def grey(x):
        x = np.asarray(x, np.float64)
        return x[..., :3].mean(axis=-1) if x.ndim == 3 else x
    a, b = grey(a), grey(b)
    if a.shape != b.shape:
        return 0.0
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    blur = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)
    mu_a, mu_b = blur(a), blur(b)
    s_a, s_b, s_ab = blur(a * a) - mu_a ** 2, blur(b * b) - mu_b ** 2, blur(a * b) - mu_a * mu_b
    m = ((2 * mu_a * mu_b + c1) * (2 * s_ab + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (s_a + s_b + c2))
    return float(m.mean())


def polygon_mask(points_yx, shape) -> np.ndarray:
    mask = np.zeros(shape, np.uint8)
    pts = np.round(np.asarray(points_yx, float)[:, ::-1]).astype(np.int32)
    cv2.fillPoly(mask, [pts], 1)
    return mask.astype(bool)


def mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


# --- per task ------------------------------------------------------------------------------------------------

def s_t0(d, p, o):
    ref = _ref(d, "labels")
    seen, declared = set(o.get("hosts_seen", [])), set(o.get("hosts_declared", []))
    return _result({"installed_by_ordinary_installer": bool(o.get("installed")), "preparation_receipt": bool(o.get("receipt")),
                    "check_image_f1_1": object_f1(ref, np.asarray(o["labels"])) == 1.0,
                    "every_download_host_declared": bool(declared) and seen <= declared},
                   undeclared=sorted(seen - declared))


def s_t1(d, p, o):
    e = p["expect"]
    pos = o.get("position_um", {})
    return _result({"value_exact": o.get("value") == e["value"],
                    "position_within_half_voxel": all(abs(float(pos.get(k, math.inf)) - e["position_um"][k]) <= e["tolerance_um"][k]
                                                      for k in ("z", "y", "x")),
                    "displayed_axes": list(o.get("displayed_axes", [])) == e["displayed_axes"]})


def s_t2(d, p, o):
    e = p["expect"]
    checks = {}
    for name, want in e["channels"].items():
        got = o.get("channels", {}).get(name, {})
        checks[f"{name}_visible"] = got.get("visible") == want["visible"]
        if want["visible"]:
            tol = e["limits_tolerance"][name]
            lim = got.get("limits") or [math.inf, math.inf]
            checks[f"{name}_limits"] = all(abs(float(g) - w) <= tol for g, w in zip(lim, want["limits"]))
            checks[f"{name}_colormap"] = str(got.get("colormap", "")).lower() == want["colormap"]
    slab = o.get("slab", {})
    checks["slab"] = (slab.get("thickness") == e["slab"]["thickness"] and slab.get("centre") == e["slab"]["centre"]
                      and slab.get("mode") == e["slab"]["mode"])
    return _result(checks)


def s_t3(d, p, o):
    e = p["expect"]
    return _result({"render_mode": o.get("render") == e["render"],
                    "threshold": abs(float(o.get("threshold", math.inf)) - e["threshold"]) <= e["threshold_tolerance"],
                    "plane": abs(float(o.get("plane_depth", math.inf)) - e["plane_depth"]) <= e["plane_tolerance"],
                    "picked_id": o.get("picked") == e["picked"], "picked_in_3d": o.get("picked_in_3d") is True})


def s_t4(d, p, o):
    e = p["expect"]
    rel = lambda a, b: abs(float(a) - b) <= 1e-6 * max(abs(b), 1e-12)
    return _result({"parent": o.get("parent") == e["parent"],
                    "vertex_value": rel(o.get("vertex_value", math.inf), e["vertex_value"]),
                    "arrow_length": rel(o.get("arrow_length", math.inf), e["arrow_length"]),
                    "out_of_slice": o.get("out_of_slice") is True,
                    "point_size_half": abs(float(o.get("point_size_factor", 0)) - 0.5) < 1e-9,
                    "grid": o.get("grid") is True})


def s_t5(d, p, o):
    return _result({"value_exact": o.get("value") == p["expect"]["value"], "from_level_0": o.get("level") == 0})


def s_t6(d, p, o):
    e = p["expect"]
    shape = tuple(e["shape"])
    centres = np.asarray(e["centres"], float)
    pts = np.asarray(o.get("points", []), float).reshape(-1, 2)
    used, matched = set(), 0
    for c in centres:                                 # greedy nearest within tolerance; centres are >= 2 radii apart
        dists = np.linalg.norm(pts - c, axis=1) if len(pts) else np.array([])
        for j in np.argsort(dists):
            if j not in used and dists[j] <= e["point_tolerance_px"]:
                used.add(j)
                matched += 1
                break
    rect_iou = mask_iou(polygon_mask(o.get("rectangle", [[0, 0]] * 4), shape), polygon_mask(e["rectangle"], shape))
    poly_iou = mask_iou(polygon_mask(o.get("polygon", [[0, 0]] * 3), shape), polygon_mask(e["polygon"], shape))
    return _result({"point_count_exact": len(pts) == len(centres), "every_point_within_3px": matched == len(centres),
                    "rectangle_iou": rect_iou >= e["iou_min"], "polygon_iou": poly_iou >= e["iou_min"],
                    "polygon_behind_rectangle": o.get("polygon_behind_rectangle") is True},
                   rectangle_iou=rect_iou, polygon_iou=poly_iou)


def s_t7(d, p, o):
    truth, start = _ref(d, "truth"), _ref(d, "start")
    merge, false, hole = (_ref(d, n).astype(bool) for n in ("merge_region", "false_region", "hole_region"))
    out = np.asarray(o["labels"])
    m = p["marks"]
    iou, t_ids, p_ids = iou_matrix(np.where(np.isin(truth, m["merged_ids"]), truth, 0), np.where(merge, out, 0))
    best = {int(t): (int(p_ids[np.argmax(iou[i])]), float(iou[i].max())) for i, t in enumerate(t_ids)} if iou.size else {}
    parts = [best.get(i, (0, 0.0)) for i in m["merged_ids"]]
    split = len({pid for pid, _ in parts}) == 2 and all(v >= p["expect"]["split_iou_min"] for _, v in parts)
    hole_ids = np.unique(out[hole])
    nucleus_h = np.unique(out[(truth == m["hole_id"]) & ~hole])
    outside = ~(merge | false | hole)
    return _result({"merged_nucleus_split": split, "false_object_deleted": not np.any(out[false]),
                    "hole_filled": len(hole_ids) == 1 and hole_ids[0] != 0 and len(nucleus_h) == 1 and hole_ids[0] == nucleus_h[0],
                    "unchanged_outside_edits_and_undo_clean": bool(np.array_equal(out[outside], start[outside]))},
                   split_ious=[v for _, v in parts])


def s_t8(d, p, o):
    f1 = object_f1(_ref(d, "labels"), np.asarray(o["labels"]), p["expect"]["iou"])
    return _result({"object_f1": f1 >= p["expect"]["f1_min"]}, f1=f1)


def _rows(o) -> list[dict]:
    if "rows" in o:
        return o["rows"]
    with open(o["csv"], newline="") as f:
        reader = csv.DictReader(f)
        cols = {c.lower(): c for c in reader.fieldnames or []}
        pick = lambda *keys: next((cols[c] for c in cols if any(k in c for k in keys)), None)
        lab, area, mean = pick("label", "id", "index"), pick("area"), pick("mean")
        if not (lab and area and mean):
            return []
        return [{"label": int(float(r[lab])), "area_um2": float(r[area]), "mean_intensity": float(r[mean])} for r in reader]


def s_t9(d, p, o):
    e = p["expect"]
    got = {r["label"]: r for r in _rows(o)}
    want = {r["label"]: r for r in e["rows"]}
    close = lambda a, b, tol: abs(a - b) <= tol * max(abs(b), 1e-12)
    rows_ok = set(got) == set(want) and len(_rows(o)) == len(want)
    return _result({"one_row_per_object": rows_ok,
                    "areas_um2": rows_ok and all(close(got[i]["area_um2"], want[i]["area_um2"], e["area_rel_tol"]) for i in want),
                    "mean_intensities": rows_ok and all(close(got[i]["mean_intensity"], want[i]["mean_intensity"], e["mean_rel_tol"]) for i in want)})


def s_t10(d, p, o):
    e = p["expect"]
    refs = sorted((d / "reference" / "keyframes").glob("*.npy")) if (d / "reference" / "keyframes").is_dir() else []
    images = o.get("frame_images", {})
    sims = [ssim(images.get(idx, images.get(str(idx), np.zeros(1))), np.load(r)) for idx, r in zip(e["keyframe_index"], refs)]
    return _result({"keyframes": o.get("keyframes") == e["keyframes"], "frames": o.get("frames") == e["frames"],
                    "reference_render_present": len(refs) == e["keyframes"],
                    "ssim_at_keyframes": len(sims) == e["keyframes"] and all(s >= e["ssim_min"] for s in sims)}, ssim=sims)


def s_t11(d, p, o):
    return _result({"saved_byte_exact": np.array_equal(np.asarray(o["saved"]), _ref(d, "after-e1"))
                    and np.asarray(o["saved"]).dtype == _ref(d, "after-e1").dtype,
                    "reopened_and_only_second_change": np.array_equal(np.asarray(o["final"]), _ref(d, "after-e2"))})


def s_t12(d, p, o):
    return _result({"max_projection_exact": np.array_equal(np.asarray(o["max_projection"]), _ref(d, "max_projection")),
                    "label_changed_in_place": np.array_equal(np.asarray(o["labels"]), _ref(d, "labels_changed")),
                    "variables_kept": o.get("answer") == p["expect"]["answer"], "kernel_alive": o.get("kernel_alive") is True})


def s_t13(d, p, o):
    return _result({"value_exact": o.get("value") == p["expect"]["value"],
                    "t7_output_unchanged": o.get("t7_output_unchanged") is True})


SCORERS = {"T0": s_t0, "T1": s_t1, "T2": s_t2, "T3": s_t3, "T4": s_t4, "T5": s_t5, "T6": s_t6, "T7": s_t7,
           "T8": s_t8, "T9": s_t9, "T10": s_t10, "T11": s_t11, "T12": s_t12, "T13": s_t13}


def score(package: Path, observed: dict, task: str | None = None) -> dict:
    """Score one attempt. `package` is <packages>/<task>/<variant>; Hooke-only tasks need only `task`."""
    task = task or _params(package)["task"]
    if task.startswith("U"):
        crit = observed.get("criteria", {})
        return _result({k: crit.get(k) is True for k in U_CRITERIA[task]})
    if task == "T14":
        raise ValueError("T14's oracle is written into the binding manifest when the extension is chosen")
    return SCORERS[task](package, _params(package), observed)
