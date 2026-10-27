"""Cellpose-SAM's device self-check (ticket 62, F-L8-1): prove a device gives Cellpose-SAM's known answer before any
result from it is shown.

Observed 2026-10-21 on the 0.2.0 candidate: in a macOS virtual machine, Cellpose-SAM on the paravirtual GPU (MPS) returned
zero cells with no error, and the app said "Outlined and measured 0 cells". The same weights, code and picture on the CPU
found the cells. A GPU that answers wrongly without raising is invisible to everything downstream, so the device is checked
by its answer, not by `torch.backends.mps.is_available()`:

1. **The probe:** the bundled CC0 specimen's 128×128 crop (assets/examples/human_mitosis.png, rows and columns 128–255;
   the crop H12, L2 and L8 used), with fixed settings (PROBE_SETTINGS).
2. **The reference:** what the CPU finds on it with the pinned weights and code: REFERENCE, one (area, row, column) per
   cell. The CPU is the reference device: it is what a mismatch falls back to.
3. **The check:** the chosen device runs the probe; its cells must match the reference (`agrees`): the same count within
   one, and every reference cell found again at about the same place and size. Agreement, not identical bytes, because
   a working GPU differs from the CPU in the last bits.
4. **The outcome**, in order: the device as it is (bfloat16, Cellpose's default), then the same device in float32, then the
   CPU, the first that agrees. A fallback is said plainly, and an outcome is kept per signature (torch, Cellpose, macOS,
   the hardware model, whether it is a virtual machine, the device, the weights) in the model's own folder (the
   published revision is read-only), so the probe runs once per machine and setup, not at every load.

A probe that can't be run (its picture missing) is not a pass: the CPU is used, and why is said. A device that raised
during the probe (out of memory, say) is not "wrong results": that is said differently and never kept, so the next load
probes again.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

PROBE_FILE = Path(__file__).resolve().parents[2] / "assets" / "examples" / "human_mitosis.png"
PROBE_CROP = (slice(128, 256), slice(128, 256))
PROBE_SHA256 = "cf19b15ea256fcda5e70aa92cb0eb98c92d229a4589007ae7fc9f95e1acac976"  # the crop's uint8 values
PROBE_SETTINGS = {"diameter": 30, "cellprob_threshold": 0.0, "flow_threshold": 0.4, "min_size": 15}
# The CPU's cells on the probe (cellpose 4.2.1.1, torch 2.14.1, cpsam_v2 0f1cc3f7…): (area px, centroid row, column).
REFERENCE: tuple[tuple[int, float, float], ...] = ((79, 13.68, 40.16), (55, 14.45, 50.05), (143, 24.58, 44.41), (112, 66.55, 86.04), (52, 87.0, 124.6), (84, 122.0, 105.5))
REFERENCE_MASKS_SHA256 = "09fa54df8e4abb2d"  # prefix of the CPU masks' SHA-256 (<u2), for the record
CENTROID_PX = 3.0
AREA_RATIO = 0.3
RECORD = ".device-check.json"
ON_CPU_WORDS = "This Mac's GPU gave wrong results for Cellpose-SAM, so it runs on the CPU, slower"
CHECK_FAILED_WORDS = "The check of this Mac's GPU for Cellpose-SAM couldn't finish, so it runs on the CPU for now, slower"
HALF_UNCHECKED_WORDS = ("This Mac's GPU couldn't be checked for Cellpose-SAM in half precision, so it runs in full precision, "
                        "where its check passed")
NO_PICTURE_WORDS = "This Mac's GPU couldn't be checked for Cellpose-SAM: its check picture couldn't be read, so it runs on the CPU, slower"
NO_BASE_WORDS = "Cellpose-SAM's own weights aren't downloaded, so this Mac's GPU can't be checked: it runs on the CPU, slower"

_lock = threading.Lock()


def probe() -> np.ndarray:
    """The probe picture, checked against its pinned values (a changed file is not the probe)."""
    from PIL import Image

    crop = np.ascontiguousarray(np.asarray(Image.open(PROBE_FILE))[PROBE_CROP])
    if hashlib.sha256(crop.tobytes()).hexdigest() != PROBE_SHA256:
        raise ValueError(f"the probe picture {PROBE_FILE.name} isn't the pinned one")
    return crop


def cells_of(masks) -> list[tuple[int, float, float]]:
    """Each cell of a label map: (area, centroid row, centroid column), by label."""
    masks = np.asarray(masks)
    out = []
    for label in np.unique(masks):
        if label == 0:
            continue
        rows, cols = np.nonzero(masks == label)
        out.append((int(rows.size), float(rows.mean()), float(cols.mean())))
    return out


def agrees(masks, reference=None) -> tuple[bool, str]:
    """Whether a device's answer on the probe is Cellpose-SAM's: the reference's count within one, and every reference
    cell found again within CENTROID_PX at an area within AREA_RATIO. Returns (agrees, what was seen)."""
    reference = REFERENCE if reference is None else reference
    found = cells_of(masks)
    if abs(len(found) - len(reference)) > 1:
        return False, f"found {len(found)} cells on the check picture, where the CPU finds {len(reference)}"
    unmatched = []
    for area, row, col in reference:
        near = [a for a, r, c in found if (r - row) ** 2 + (c - col) ** 2 <= CENTROID_PX ** 2]
        if not any(abs(a - area) <= AREA_RATIO * area for a in near):
            unmatched.append((round(row), round(col)))
    if unmatched:
        return False, f"{len(unmatched)} of the {len(reference)} cells the CPU finds were missing or misshapen"
    return True, f"found the {len(reference)} cells the CPU finds on the check picture"


def signature(device, weights: str | os.PathLike | None) -> dict:
    """What a verdict holds for: the software, the machine and the device."""
    import importlib.metadata

    import torch

    def version(name):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return None

    w = Path(weights) if weights else None
    return {"torch": torch.__version__, "cellpose": version("cellpose"), "macos": platform.mac_ver()[0] or platform.platform(),
            "machine": platform.machine(), **hardware(), "device": str(getattr(device, "type", device)),
            "weights": f"{w.name}:{w.stat().st_size}" if w is not None and w.is_file() else None}


def hardware() -> dict:
    """The Mac's model (`hw.model`) and whether this is a virtual machine (`kern.hv_vmm_present`): a verdict made on one
    is never reused on another, even with the same software. Unreadable values are kept as unknown."""
    import subprocess

    out = {}
    for key, name in (("hw.model", "model"), ("kern.hv_vmm_present", "vm")):
        try:
            out[name] = subprocess.run(["/usr/sbin/sysctl", "-n", key], capture_output=True, text=True, timeout=5).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            out[name] = None
    return out


def _key(sig: dict) -> str:
    return hashlib.sha256(json.dumps(sig, sort_keys=True).encode()).hexdigest()[:16]


def _read(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path | None, data: dict) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")  # each process its own, so two writers never share one
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
        os.replace(tmp, path)
    except OSError:
        pass  # a verdict that can't be kept is checked again next time, never assumed


def choose(make: Callable, device, *, weights=None, record: Path | None = None, run_probe: Callable | None = None):
    """The model to use and its proof: `make(device, bfloat16)` builds a Cellpose model; the first configuration whose
    probe answer agrees is used (the device in bfloat16, then in float32, then the CPU). A verdict kept for this exact
    signature is reused without probing. Returns (model, proof); the proof says the device used, how it was checked,
    and, after a fallback, the plain words to show."""
    proof, model = verdict(make, device, weights=weights, record=record, run_probe=run_probe)
    return (model if model is not None else make(placed(proof, device), proof["dtype"] == "bfloat16")), proof


def placed(proof: dict, device):
    """The device a verdict says to run on: the checked device, or the CPU."""
    import torch

    return torch.device("cpu") if proof["device"] == "cpu" else device


def verdict(make: Callable, device, *, weights=None, record: Path | None = None, run_probe: Callable | None = None):
    """(proof, model): where Cellpose-SAM may run on this machine (`choose`). A model is built only when the device has to
    be probed, and then the one that passed is returned (None when the answer was known without building one: the CPU,
    or a verdict kept for this signature), so a caller that wants other weights builds only its own."""
    import torch

    run_probe = run_probe or (lambda model, picture: model.eval(picture, batch_size=1, **PROBE_SETTINGS)[0])
    if getattr(device, "type", str(device)) == "cpu":
        return {"device": "cpu", "dtype": "bfloat16", "checked": "the CPU is the reference device"}, None
    sig = signature(device, weights)
    with _lock:
        kept = _read(record).get(_key(sig))
    if kept:
        return {k: v for k, v in kept.items() if k != "signature"} | {"kept": True}, None
    tried = []
    try:
        picture = probe()
    except (OSError, ValueError) as exc:
        return {"device": "cpu", "dtype": "bfloat16", "checked": f"the device check couldn't run ({exc})",
                "says": NO_PICTURE_WORDS}, None
    for dev, bf16 in ((device, True), (device, False)):
        t0 = time.perf_counter()
        raised = False
        try:
            model = make(dev, bf16)
            ok, seen = agrees(run_probe(model, picture))
        except Exception as exc:  # the device didn't answer: not proven, but not "wrong results" either
            model, ok, raised = None, False, True
            seen = f"the check couldn't finish: {type(exc).__name__}: {exc}"[:240]
        tried.append({"device": str(dev), "dtype": "bfloat16" if bf16 else "float32", "agrees": ok, "seen": seen,
                      "raised": raised, "seconds": round(time.perf_counter() - t0, 2)})
        if ok:
            proof = {"device": str(dev), "dtype": "bfloat16" if bf16 else "float32", "checked": seen,
                     "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "tried": tried}
            if not bf16:
                crashed = tried[0]["raised"]
                # Wrong answers in half precision are a verdict; a crash there is not: said as such and never kept.
                proof["says"] = HALF_UNCHECKED_WORDS if crashed else (
                    "This Mac's GPU gave wrong results for Cellpose-SAM in half precision, so it runs in full precision")
                if crashed:
                    return proof, model
            _keep(record, sig, proof)
            return proof, model
        del model
    if any(t["raised"] for t in tried):
        # Out of memory or a driver error is not a verdict on the device's answers: said as such, never kept.
        return {"device": "cpu", "dtype": "bfloat16", "checked": "the CPU is the reference device",
                "says": CHECK_FAILED_WORDS, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "tried": tried}, None
    proof = {"device": "cpu", "dtype": "bfloat16", "checked": "the CPU is the reference device",
             "says": ON_CPU_WORDS, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "tried": tried}
    _keep(record, sig, proof)
    return proof, None


def _keep(record: Path | None, sig: dict, proof: dict) -> None:
    with _lock:
        data = _read(record)
        data[_key(sig)] = {**proof, "signature": sig}
        _write(record, data)
