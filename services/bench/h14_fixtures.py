"""Task data packages for the H14 study (qualification/h14-study-design.md §4.2), generated deterministically.

`build(out, tasks, scale)` writes, for each task and variant D1/D2: the data files, `params.json` (the variant's
values and the oracle's reference answers), `card.txt` (the participant's goal, from bench.h14_tasks), and a
`reference/` folder of reference outputs for bench.h14_scoring. `PACKAGE.sha256` lists every file's SHA-256 for the
binding manifest (§3.3).

D1 and D2 match by construction (§4.2): the same shapes, dtypes, object counts (within 10%), size distribution and
contrast, with different positions and values, because each comes from the same generator with a different seed.

Real microscopy (§4.2): the final packages for T7-T9 use BBBC039 (CC0) and T5/T13 tile real data. Pass
`real_tiles` (an image) and `nuclei` (image and ground-truth labels) to use them; the bound manifest records their
hashes. Without them every package is synthetic, which is what the dry run and the tests use, and the package says so
in `params.json` ("source": "synthetic"). A synthetic package cannot satisfy the frozen design's real-data rows; the
binding manifest's check refuses it for those tasks (bench.qualification_evidence).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import tifffile

from bench import h14_tasks

VARIANT_SEEDS = {"D1": 101, "D2": 202}
REAL_DATA_TASKS = ("T5", "T7", "T8", "T9", "T13")


def _rng(task: str, variant: str) -> np.random.Generator:
    return np.random.default_rng([VARIANT_SEEDS[variant], int(task[1:]) if task[1:].isdigit() else 99])


def _tif(path: Path, data: np.ndarray, **kw) -> None:
    """Greyscale planes, never RGB: a 3-plane stack must stay three depth planes (tifffile guesses RGB otherwise)."""
    tifffile.imwrite(path, data, photometric="minisblack", **kw)


def _uuid(task: str, variant: str) -> str:
    """A fixed OME UUID per file, so a rebuild is byte-identical (tifffile otherwise makes a new one each time)."""
    h = hashlib.sha256(f"h14/{task}/{variant}".encode()).hexdigest()
    return f"urn:uuid:{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1, sort_keys=True, default=_jsonable) + "\n")


def _jsonable(v):
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    if isinstance(v, np.ndarray):
        return v.tolist()
    raise TypeError(type(v))


# --- shared synthetic pieces ----------------------------------------------------------------------------------

def nuclei(rng: np.random.Generator, shape=(256, 256), count=24, radius=(9, 15)) -> tuple[np.ndarray, np.ndarray]:
    """Non-overlapping elliptical nuclei: (uint16 image, uint32 labels 1..n)."""
    labels = np.zeros(shape, np.uint32)
    placed = 0
    for _ in range(count * 60):
        if placed == count:
            break
        ry, rx = rng.integers(radius[0], radius[1] + 1, 2)
        cy = int(rng.integers(ry + 2, shape[0] - ry - 2))
        cx = int(rng.integers(rx + 2, shape[1] - rx - 2))
        mask = np.zeros(shape, np.uint8)
        cv2.ellipse(mask, (cx, cy), (int(rx), int(ry)), float(rng.uniform(0, 180)), 0, 360, 1, -1)
        grown = cv2.dilate(mask, np.ones((5, 5), np.uint8))
        if np.any(labels[grown.astype(bool)]):
            continue
        placed += 1
        labels[mask.astype(bool)] = placed
    brightness = rng.uniform(1500, 3500, placed + 1)
    brightness[0] = 300
    img = brightness[labels]
    img = cv2.GaussianBlur(img.astype(np.float32), (0, 0), 1.2)
    img = img + rng.normal(0, 40, shape)
    return np.clip(img, 0, 65535).astype(np.uint16), labels


def centroid(labels: np.ndarray, i: int) -> tuple[float, ...]:
    return tuple(float(c.mean()) for c in np.nonzero(labels == i))


# --- per task generators ----------------------------------------------------------------------------------------
# Each returns (params, reference arrays) and writes its data files into `d`.

def t0(d: Path, rng, scale):
    img, lab = nuclei(rng, (64, 64), count=4, radius=(6, 9))
    _tif(d / "check.tif", img)
    return {"check_image": "check.tif", "declared_hosts": "bound at binding (the package's declared downloads)"}, {"labels": lab}


FIVE_D = {"small": (2, 3, 5, 32, 40), "full": (6, 3, 21, 256, 320)}
SPACING = {"z": 2.0, "y": 0.65, "x": 0.65}
CHANNELS = ("DAPI", "GFP", "mCherry")


def _five_d(rng, scale):
    T, C, Z, Y, X = FIVE_D[scale]
    t, c, z = np.meshgrid(np.arange(T), np.arange(C), np.arange(Z), indexing="ij")
    data = np.empty((T, C, Z, Y, X), np.uint16)
    data[...] = ((t * 1000 + c * 100 + z) % 4000)[..., None, None]       # index-encoded background (§3 fixture)
    t_hit, c_hit = int(rng.integers(0, T)), int(rng.integers(0, C))
    objs = []
    for k in range(3):
        zz, yy, xx = int(rng.integers(0, Z)), int(rng.integers(3, Y - 3)), int(rng.integers(3, X - 3))
        value = int(60000 - 1000 * k - rng.integers(0, 500))
        data[t_hit, c_hit, zz, yy - 1:yy + 2, xx - 1:xx + 2] = value
        objs.append({"z": zz, "y": yy, "x": xx, "value": value})
    return data, t_hit, c_hit, objs


def t1(d: Path, rng, scale):
    data, t_hit, c_hit, objs = _five_d(rng, scale)
    _tif(d / "five-d.tif", data, ome=True, metadata={
        "axes": "TCZYX", "UUID": _uuid("T1", d.name), "Channel": {"Name": list(CHANNELS)},
        "PhysicalSizeZ": SPACING["z"], "PhysicalSizeY": SPACING["y"], "PhysicalSizeX": SPACING["x"]})
    best = max(objs, key=lambda o: o["value"])
    order = ["depth and x", "y and depth"][int(rng.integers(0, 2))]
    params = {"t": t_hit, "channel": CHANNELS[c_hit], "order": order,
              "expect": {"value": best["value"],
                         "position_um": {"z": best["z"] * SPACING["z"], "y": best["y"] * SPACING["y"],
                                         "x": best["x"] * SPACING["x"]},
                         "tolerance_um": {k: 0.5 * v for k, v in SPACING.items()},
                         "displayed_axes": {"depth and x": ["z", "x"], "y and depth": ["y", "z"]}[order]},
              "marked": objs}
    return params, {}


def t2(d: Path, rng, scale):
    data, *_ = _five_d(rng, scale)
    _tif(d / "five-d.tif", data, ome=True, metadata={"axes": "TCZYX", "UUID": _uuid("T2", d.name),
                                                       "Channel": {"Name": list(CHANNELS)}})
    Z = data.shape[2]
    z = int(rng.integers(2, Z - 2))
    order = rng.permutation(3)
    lo1, lo2 = (int(v) for v in rng.integers(100, 800, 2))
    params = {"sample": "bound at binding (the app's cells sample)", "slab": 5, "z": z,
              "c1": CHANNELS[order[0]], "r1": f"{lo1}–{lo1 + 3000}", "cmap1": "magenta",
              "c2": CHANNELS[order[1]], "r2": f"{lo2}–{lo2 + 2500}", "cmap2": "green", "c3": CHANNELS[order[2]],
              "expect": {"channels": {CHANNELS[order[0]]: {"limits": [lo1, lo1 + 3000], "colormap": "magenta", "visible": True},
                                      CHANNELS[order[1]]: {"limits": [lo2, lo2 + 2500], "colormap": "green", "visible": True},
                                      CHANNELS[order[2]]: {"visible": False}},
                         "slab": {"thickness": 5, "centre": z, "mode": "max"},
                         # "within ±1% of range" (§4.1): 1% of that channel's data range (max - min)
                         "limits_tolerance": {name: 0.01 * float(data[:, i].max() - data[:, i].min())
                                              for i, name in enumerate(CHANNELS)}}}
    return params, {}


def t3(d: Path, rng, scale):
    n = 32 if scale == "small" else 64
    zz, yy, xx = np.mgrid[:n, :n, :n]
    labels = np.zeros((n, n, n), np.uint16)
    cy, cx = n // 2 + int(rng.integers(-3, 4)), n // 2 + int(rng.integers(-3, 4))
    for k, cz in enumerate((n // 4, n // 2, 3 * n // 4), start=1):     # three objects stacked along the view axis
        r = n // 6 + k
        labels[((zz - cz) ** 2 + (yy - cy) ** 2 + (xx - cx) ** 2) <= r * r] = k
    image = (labels.astype(np.float32) * 900 + rng.normal(200, 30, labels.shape)).clip(0, 65535).astype(np.uint16)
    target = int(rng.integers(1, 4))
    marker = [int(v) for v in np.argwhere(labels == target)[len(np.argwhere(labels == target)) // 2]]
    _tif(d / "volume.tif", image)
    _tif(d / "volume-labels.tif", labels)
    np.save(d / "marker.npy", np.array([marker], float))
    params = {"threshold": 1200, "depth": n // 2,
              "expect": {"render": "iso", "threshold": 1200, "threshold_tolerance": 0.02 * 1200,
                         "plane_depth": n // 2, "plane_tolerance": 1, "picked": target, "picked_in_3d": True}}
    return params, {}


def t4(d: Path, rng, scale):
    tracks, child, parent = [], 4, int(rng.integers(1, 3))
    for tid in (1, 2, 3):
        for t in range(5):
            tracks.append([tid, t, 10 + 5 * tid + t, 20 + t])
    for tid in (4, 5):                                                     # two daughters after the division
        for t in range(5, 9):
            tracks.append([tid, t, 10 + 5 * parent + t + (tid - 4) * 3, 20 + t])
    graph = {4: [parent], 5: [parent]}
    verts = rng.uniform(0, 30, (40, 3))
    faces = np.array([[i, (i + 1) % 40, (i + 7) % 40] for i in range(40)])
    values = np.round(rng.uniform(0, 1, 40), 4)
    vertex = int(rng.integers(0, 40))
    vectors = np.zeros((12, 2, 3))
    vectors[:, 0] = rng.uniform(0, 30, (12, 3))
    vectors[:, 1] = rng.uniform(-4, 4, (12, 3))
    arrow = int(rng.integers(0, 12))
    points = rng.uniform(0, 30, (20, 3))
    np.savez(d / "scene.npz", tracks=np.array(tracks, float), surface_vertices=verts, surface_faces=faces,
             surface_values=values, vectors=vectors, points=points, marked_vertex=np.array([vertex]),
             marked_arrow=np.array([arrow]))
    _write_json(d / "scene-graph.json", {str(k): v for k, v in graph.items()})
    params = {"child": child, "expect": {"parent": parent, "vertex_value": float(values[vertex]),
                                         "arrow_length": float(np.linalg.norm(vectors[arrow, 1])),
                                         "out_of_slice": True, "point_size_factor": 0.5, "grid": True}}
    return params, {}


BIG = {"small": (8, 512, 512), "full": (64, 16384, 10240)}             # full: 20 GiB decoded at uint16 (>= 4 x 5 GiB)
CHUNK = (1, 512, 512)


def big_value(z, y, x):
    """The pixel value at full resolution, so the oracle never needs to read the data back."""
    return ((np.asarray(x, np.int64) * 7 + np.asarray(y, np.int64) * 13 + np.asarray(z, np.int64) * 101) % 65521).astype(np.uint16)


def write_ome_zarr(path: Path, shape: tuple[int, int, int], levels: int = 3, tile: np.ndarray | None = None) -> None:
    """A zarr v2 OME-Zarr 0.4 multiscale image, written chunk by chunk, uncompressed (no codec dependency).

    Level k keeps every 2^k-th pixel in y and x. Without `tile` the values are big_value(); with a real tile they are
    the tile repeated, offset so no two repeats are identical, and the oracle reads them with tile_value().
    """
    path.mkdir(parents=True, exist_ok=True)
    (path / ".zgroup").write_text('{"zarr_format": 2}')
    datasets = []
    for k in range(levels):
        f = 2 ** k
        shp = (shape[0], -(-shape[1] // f), -(-shape[2] // f))
        lvl = path / str(k)
        lvl.mkdir(exist_ok=True)
        (lvl / ".zarray").write_text(json.dumps({"zarr_format": 2, "shape": list(shp), "chunks": list(CHUNK),
                                                 "dtype": "<u2", "compressor": None, "fill_value": 0, "order": "C",
                                                 "filters": None, "dimension_separator": "/"}))
        for z in range(shp[0]):
            for cy in range(-(-shp[1] // CHUNK[1])):
                for cx in range(-(-shp[2] // CHUNK[2])):
                    yy = (np.arange(cy * CHUNK[1], (cy + 1) * CHUNK[1]) * f)[:, None]
                    xx = (np.arange(cx * CHUNK[2], (cx + 1) * CHUNK[2]) * f)[None, :]
                    block = big_value(z, yy, xx) if tile is None else tile_value(tile, z, yy, xx)
                    out = lvl / str(z) / str(cy)
                    out.mkdir(parents=True, exist_ok=True)
                    (out / str(cx)).write_bytes(np.ascontiguousarray(block, "<u2").tobytes())
        datasets.append({"path": str(k), "coordinateTransformations": [{"type": "scale", "scale": [2.0, 0.65 * f, 0.65 * f]}]})
    (path / ".zattrs").write_text(json.dumps({"multiscales": [{"version": "0.4", "name": "big", "datasets": datasets,
        "axes": [{"name": "z", "type": "space", "unit": "micrometer"}, {"name": "y", "type": "space", "unit": "micrometer"},
                 {"name": "x", "type": "space", "unit": "micrometer"}]}]}))


def tile_value(tile: np.ndarray, z, y, x):
    ty, tx = tile.shape
    return ((tile[np.asarray(y) % ty, np.asarray(x) % tx].astype(np.int64)
             + (np.asarray(y) // ty) * 3 + (np.asarray(x) // tx) * 5 + np.asarray(z) * 11) % 65521).astype(np.uint16)


def t5(d: Path, rng, scale, tile=None):
    shape = BIG[scale]
    write_ome_zarr(d / "big.ome.zarr", shape, tile=tile)
    z, y, x = int(rng.integers(0, shape[0])), int(rng.integers(0, shape[1])), int(rng.integers(0, shape[2]))
    value = int((big_value(z, y, x) if tile is None else tile_value(tile, z, y, x)))
    return {"x": x, "y": y, "z": z, "source": "synthetic" if tile is None else "real tile",
            "expect": {"value": value, "level": 0}}, {}


def t6(d: Path, rng, scale):
    img, lab = nuclei(rng, (512, 512), count=10)
    centres = [centroid(lab, i) for i in range(1, int(lab.max()) + 1)]
    rect = [[60, 60], [60, 180], [160, 180], [160, 60]]
    rect = (np.array(rect) + rng.integers(0, 200, 2)).tolist()
    poly = (np.array([[300, 300], [280, 380], [340, 430], [400, 390], [390, 320]]) + rng.integers(-40, 40, 2)).tolist()
    _tif(d / "cells.tif", img)
    return {"n": len(centres), "expect": {"centres": centres, "point_tolerance_px": 3, "rectangle": rect,
                                          "polygon": poly, "iou_min": 0.85, "polygon_behind_rectangle": True,
                                          "shape": [512, 512]}}, {}


def edit_targets(rng, img, lab):
    """T7's errors made from ground-truth nuclei: a merge (M), a false object (F), a hole (H), and a nucleus to
    erase and restore. Returns the edited labels the participant starts from, and the marks."""
    ids = list(range(1, int(lab.max()) + 1))
    cents = {i: np.array(centroid(lab, i)) for i in ids}
    a, b = min(((i, j) for i in ids for j in ids if i < j), key=lambda p: np.linalg.norm(cents[p[0]] - cents[p[1]]))
    start = lab.copy()
    bridge = np.zeros(lab.shape, np.uint8)
    cv2.line(bridge, tuple(int(v) for v in cents[a][::-1]), tuple(int(v) for v in cents[b][::-1]), 1, 5)
    merge_region = (lab == a) | (lab == b) | ((bridge > 0) & (lab == 0))
    start[merge_region] = a
    others = [i for i in ids if i not in (a, b)]
    h, mistake = others[0], others[1]
    hy, hx = (int(v) for v in cents[h])
    hole = np.zeros(lab.shape, np.uint8)
    cv2.circle(hole, (hx, hy), 3, 1, -1)
    hole_region = hole.astype(bool) & (lab == h)
    start[hole_region] = 0
    free = np.argwhere(cv2.dilate((lab > 0).astype(np.uint8), np.ones((25, 25), np.uint8)) == 0)
    fy, fx = free[int(rng.integers(0, len(free)))]
    false = np.zeros(lab.shape, np.uint8)
    cv2.circle(false, (int(fx), int(fy)), 6, 1, -1)
    false_region = false.astype(bool)
    start[false_region] = int(lab.max()) + 1
    marks = {"M": [float(v) for v in (cents[a] + cents[b]) / 2], "F": [float(fy), float(fx)], "H": [float(hy), float(hx)],
             "X": [float(v) for v in cents[mistake]],
             "merged_ids": [a, b], "merged_into": a, "hole_id": h, "false_id": int(lab.max()) + 1, "mistake": mistake}
    regions = {"merge": merge_region, "false": false_region, "hole": hole_region}
    return start, marks, regions


def t7(d: Path, rng, scale, real=None):
    img, lab = real if real is not None else nuclei(rng)
    start, marks, regions = edit_targets(rng, img, lab)
    _tif(d / "nuclei.tif", img)
    _tif(d / "nuclei-labels.tif", start)
    params = {"mistake": marks["mistake"], "marks": marks, "source": "synthetic" if real is None else "BBBC039",
              "expect": {"split_iou_min": 0.80, "outside_edits_unchanged": True}}
    return params, {"truth": lab, "start": start, "merge_region": regions["merge"], "false_region": regions["false"],
                    "hole_region": regions["hole"]}


def t8(d: Path, rng, scale, real=None):
    img, lab = real if real is not None else nuclei(rng)
    _tif(d / "nuclei.tif", img)
    return {"diameter": 24, "channel": "nuclei", "source": "synthetic" if real is None else "BBBC039",
            "expect": {"f1_min": 0.98, "iou": 0.5,
                       "reference": "reference/labels.npy is the reference Cellpose-SAM run at binding; in a synthetic "
                                    "package it is the ground truth, for the dry run only"}}, {"labels": lab}


def t9(d: Path, rng, scale, real=None):
    img, lab = real if real is not None else nuclei(rng)
    px = 0.65
    _tif(d / "nuclei.tif", img, resolution=(1 / px, 1 / px), metadata={"unit": "um"})
    _tif(d / "nuclei-labels.tif", lab)
    ids = np.arange(1, int(lab.max()) + 1)
    counts = np.bincount(lab.ravel(), minlength=len(ids) + 1)[1:]
    sums = np.bincount(lab.ravel(), weights=img.ravel().astype(np.float64), minlength=len(ids) + 1)[1:]
    rows = [{"label": int(i), "area_um2": float(counts[i - 1] * px * px), "mean_intensity": float(sums[i - 1] / counts[i - 1])}
            for i in ids]
    return {"pixel_um": px, "source": "synthetic" if real is None else "BBBC039",
            "expect": {"rows": rows, "area_rel_tol": 1e-9, "mean_rel_tol": 1e-6}}, {}


def t10(d: Path, rng, scale):
    T, Z, Y, X = (4, 8, 32, 32) if scale == "small" else (10, 24, 128, 128)
    data = rng.normal(300, 20, (T, Z, Y, X)).astype(np.float32)
    for t in range(T):
        data[t, :, Y // 4 + t:Y // 4 + t + 6, X // 3:X // 3 + 6] += 3000
    _tif(d / "timelapse.tif", data.clip(0, 65535).astype(np.uint16), metadata={"axes": "TZYX"})
    steps = 15
    # Synthetic stand-ins for the reference render's keyframes (the real ones come from the reference render at binding)
    key_t = (0, T // 2, T - 1)
    frames = {f"keyframes/{i}": np.repeat((data[t].max(axis=0) / 3300 * 255).clip(0, 255).astype(np.uint8)[..., None], 3, -1)
              for i, t in enumerate(key_t)}
    keys = ["2D, time point 0, whole image", f"3D, time point {T // 2}, turned 45 degrees", f"3D, time point {T - 1}, turned 90 degrees"]
    return {"k1": keys[0], "k2": keys[1], "k3": keys[2], "steps": steps,
            "expect": {"keyframes": 3, "frames": 2 * steps + 1, "keyframe_index": [0, steps, 2 * steps], "ssim_min": 0.95,
                       "reference": "reference/keyframes/*.npy come from the reference render at binding; synthetic here"}}, frames


def t11(d: Path, rng, scale):
    img, lab = nuclei(rng)
    e1, e2 = (int(v) for v in rng.choice(np.arange(1, int(lab.max()) + 1), 2, replace=False))
    _tif(d / "nuclei.tif", img)
    _tif(d / "nuclei-labels.tif", lab)
    after1 = lab.copy()
    after1[after1 == e1] = 0
    after2 = after1.copy()
    after2[after2 == e2] = 0
    return {"marks": {"E1": centroid(lab, e1), "E2": centroid(lab, e2)}, "e1": e1, "e2": e2,
            "expect": {"saved_equals": "reference/after-e1.npy", "final_equals": "reference/after-e2.npy"}}, \
        {"after-e1": after1, "after-e2": after2}


def t12(d: Path, rng, scale):
    img, lab = nuclei(rng, (64, 64), count=12, radius=(4, 7))
    vol = np.stack([img, np.roll(img, 3, 1), np.roll(img, -2, 0)])
    labels3 = np.stack([lab, lab, lab])
    old = int(rng.integers(1, int(lab.max()) + 1))
    new = int(lab.max()) + 5
    answer = int(rng.integers(100, 999))
    _tif(d / "stack.tif", vol)
    _tif(d / "stack-labels.tif", labels3)
    (d / "long_loop.py").write_text("import time\nfor i in range(3600):\n    time.sleep(1)\n")
    changed = labels3.copy()
    changed[changed == old] = new
    return {"old": old, "new": new, "answer": answer,
            "expect": {"answer": answer, "kernel_alive": True}}, {"max_projection": vol.max(axis=0), "labels_changed": changed}


def t13(d: Path, rng, scale, tile=None):
    shape = (4, 512, 512) if scale == "small" else (16, 4096, 4096)
    write_ome_zarr(d / "remote.ome.zarr", shape, tile=tile)
    z, y, x = int(rng.integers(0, shape[0])), int(rng.integers(0, shape[1])), int(rng.integers(0, shape[2]))
    value = int(big_value(z, y, x) if tile is None else tile_value(tile, z, y, x))
    return {"x": x, "y": y, "z": z, "source": "synthetic" if tile is None else "IDR tile",
            "serve": "python -m bench.h14_remote_server remote.ome.zarr (cuts after the bound request count)",
            "expect": {"value": value, "t7_output_unchanged": True}}, {}


GENERATORS = {"T0": t0, "T1": t1, "T2": t2, "T3": t3, "T4": t4, "T5": t5, "T6": t6, "T7": t7, "T8": t8, "T9": t9,
              "T10": t10, "T11": t11, "T12": t12, "T13": t13}


def build(out: Path, tasks=None, scale: str = "small", tile: np.ndarray | None = None,
          nuclei_sets: dict | None = None) -> dict:
    """Write the packages; return {relative path: sha256}. `nuclei_sets` maps variant -> (image, labels) of real data."""
    out.mkdir(parents=True, exist_ok=True)
    for task in tasks or GENERATORS:
        for variant in VARIANT_SEEDS:
            d = out / task / variant
            (d / "reference").mkdir(parents=True, exist_ok=True)
            rng = _rng(task, variant)
            gen = GENERATORS[task]
            kwargs = {}
            if task in ("T5", "T13"):
                kwargs["tile"] = tile
            if task in ("T7", "T8", "T9") and nuclei_sets:
                kwargs["real"] = nuclei_sets[variant]
            params, refs = gen(d, rng, scale, **kwargs)
            params.update(task=task, variant=variant, scale=scale)
            params.setdefault("source", "synthetic")
            for name, arr in refs.items():
                (d / "reference" / f"{name}.npy").parent.mkdir(parents=True, exist_ok=True)
                np.save(d / "reference" / f"{name}.npy", arr)
            _write_json(d / "params.json", params)
            marks = {k: v for k, v in params.get("marks", {}).items() if len(k) <= 2 and isinstance(v, (list, tuple))}
            if marks:   # the letters the card names, as a points file the moderator opens over the image (§4.1 start)
                (d / "marks.csv").write_text("mark,y,x\n" + "".join(f"{k},{v[0]:.1f},{v[1]:.1f}\n" for k, v in marks.items()))
            text_params = {k: v for k, v in params.items() if isinstance(v, (str, int, float))}
            (d / "card.txt").write_text(h14_tasks.card(task, text_params) + "\n")
    hashes = {}
    for f in sorted(p for p in out.rglob("*") if p.is_file() and p.name != "PACKAGE.sha256"):
        hashes[str(f.relative_to(out))] = hashlib.sha256(f.read_bytes()).hexdigest()
    (out / "PACKAGE.sha256").write_text("".join(f"{h}  {p}\n" for p, h in hashes.items()))
    return hashes


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the H14 task data packages")
    ap.add_argument("out", type=Path)
    ap.add_argument("--scale", choices=("small", "full"), default="small")
    ap.add_argument("--tasks", nargs="*")
    args = ap.parse_args(argv)
    hashes = build(args.out, args.tasks, args.scale)
    print(f"{len(hashes)} files; hashes in {args.out / 'PACKAGE.sha256'}")


if __name__ == "__main__":
    main()
