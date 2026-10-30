"""Comparing methods, not only models (ticket 59's MT6; the butterfly-wing forum case): A, membrane only; B, including
the fringe scales; optionally C, another declared method.

Each arm declares the one factor it varies (FACTORS) and says what it is, in a sentence. Arms that differ in their
*boundary definition* measure different biological things, so they are never ranked as more or less accurate against
each other: the summary says how far apart they are, never which is right. Model or algorithm arms that measure the same
definition can be compared for agreement, which is still not accuracy (ADR 0020).

`summarize` takes each arm's object mask (its labels, after its own corrections) on the same frozen source and
calibration, and gives each arm's calibrated area (calibrated_area.py; pixel counts when the calibration can't be
applied, said), the absolute and relative difference from arm A, the disagreement (pixels in one arm's mask and not
another's, with their area) and each arm's corrections as given. `choose` records the protocol chosen with its rationale,
keeping every alternative and the evidence; `export` writes all of it, with the source and calibration identities.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np

from . import calibrated_area

FACTORS = ("boundary-definition", "algorithm", "settings", "version")
FORMAT = "hooke-method-comparison-v1"
# Arms A to F (Part 2.5, W5: a workflow's comparison step takes up to six models; a record of two or three reads as before).
ARM_IDS = "abcdef"
MIN_ARMS, MAX_ARMS = 2, len(ARM_IDS)


def arm_words() -> str:
    """The arms a comparison takes, in words, for its refusals."""
    return f"compare {MIN_ARMS} to {MAX_ARMS} arms, A, B and up to {ARM_IDS[-1].upper()}"


class Invalid(ValueError):
    pass


def summarize(source: dict, calibration: dict | None, arms: list[dict], axes: tuple[str, ...] = ("y", "x")) -> dict:
    """`source`: the frozen input {id, revision, digest}; `arms`: [{id, factor, says, mask (bool array), corrections}]."""
    if not MIN_ARMS <= len(arms) <= MAX_ARMS or [a.get("id") for a in arms] != list(ARM_IDS[:len(arms)]):
        raise Invalid(arm_words())
    if not all(source.get(k) for k in ("id", "revision", "digest")):
        raise Invalid("freeze the source first: its id, revision and digest")
    shapes = {np.asarray(a["mask"]).shape for a in arms}
    if len(shapes) != 1:
        raise Invalid("every arm measures the same frozen source: their masks differ in size")
    for a in arms:
        if a.get("factor") not in FACTORS or not str(a.get("says") or "").strip():
            raise Invalid(f"arm {a.get('id', '?').upper()} declares the factor it varies ({', '.join(FACTORS)}) and "
                          "says what it is")
    masks = {a["id"]: np.asarray(a["mask"], bool) for a in arms}
    counts = {key: int(mask.sum()) for key, mask in masks.items()}
    disagreement = {a["id"]: (int((masks["a"] & ~masks[a["id"]]).sum()),
                              int((masks[a["id"]] & ~masks["a"]).sum())) for a in arms[1:]}
    return from_counts(source, calibration, arms, counts, disagreement, axes)


def from_counts(source, calibration, arms, counts, disagreement, axes):
    """Shared arithmetic for in-memory masks and admitted region-by-region native masks.

    Missing arms remain unavailable; in particular, a failed A never changes the baseline.
    Counts describe nonzero foreground, not object IDs or scientific accuracy.
    """
    import math
    size, why = calibrated_area.element_size(calibration, axes)
    unit = calibrated_area.unit(len(axes)) if size is not None else ("voxel" if len(axes) == 3 else "pixel")

    def amount(pixels):
        if pixels is None:
            return None
        if type(pixels) is not int or pixels < 0:
            raise Invalid("Foreground counts must be nonnegative integers")
        result = float(pixels) * size if size is not None else float(pixels)
        if not math.isfinite(result):
            raise Invalid("Calibrated size exceeds its numeric range; correct calibration or select a smaller region")
        return result

    rows = [{"id": a["id"], "factor": a["factor"], "says": a["says"], "pixels": counts.get(a["id"]),
             "area": amount(counts.get(a["id"])), "unit": unit, "corrections": list(a.get("corrections") or [])}
            for a in arms]
    base = rows[0]
    differences = []
    for row in rows[1:]:
        delta = None if base['area'] is None or row['area'] is None else row['area'] - base['area']
        relative = delta / base['area'] if delta is not None and base['area'] else None
        if relative is not None and not math.isfinite(relative):
            raise Invalid("Relative difference exceeds its numeric range; inspect calibration and masks")
        differences.append({'arm':row['id'],'absolute':delta,'unit':unit,'relative':relative})
    differences_by_mask = [{'arms':['a',row['id']], 'onlyA':amount(disagreement.get(row['id'],(None,None))[0]),
                           f"only{row['id'].upper()}":amount(disagreement.get(row['id'],(None,None))[1]), 'unit':unit}
                          for row in rows[1:]]
    definitions = any(r['factor'] == 'boundary-definition' for r in rows)
    return {'format':FORMAT, 'source':dict(source), 'calibration':calibration, 'calibrated':size is not None,
            **({'why':why} if why else {}), 'arms':rows, 'differences':differences, 'disagreement':differences_by_mask,
            'ranked':False, 'says':('These arms include boundary definitions: never which is more accurate. Agreement is not accuracy; '
                                  'choose the protocol your question needs') if definitions else
                                 'Agreement between arms is not accuracy (ADR 0020)', 'chosen':None}

def choose(summary: dict, arm: str, rationale: str, *, by: str = "you") -> dict:
    """The protocol chosen, with its rationale; the alternatives and the evidence stay in the record."""
    if arm not in {r["id"] for r in summary["arms"]}:
        raise Invalid(f"there is no arm {arm.upper()}")
    if not rationale.strip():
        raise Invalid("say why this protocol fits the question: the rationale is kept with it")
    chosen = next(r for r in summary["arms"] if r["id"] == arm)
    return {**summary, "chosen": {"arm": arm, "factor": chosen["factor"], "says": chosen["says"],
                                  "rationale": rationale.strip(), "by": by, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}}


def export(summary: dict, path: Path) -> dict:
    """The whole comparison as JSON (arms, areas, units, differences, disagreement, corrections, choice and rationale,
    source and calibration), with its hash."""
    text = json.dumps(summary, indent=1, sort_keys=True, default=float) + "\n"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)
    return {"file": str(path), "sha256": hashlib.sha256(text.encode()).hexdigest()}
