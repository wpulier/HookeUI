"""N models, one comparison (Part 2.5, W5: "Have a researcher run 5 different classifying models… report comparing").

The one interface every comparer uses (the brain's `compare_models`, a researcher's hands' `classify`, an experiment):
each model does the same work on the same input through the runner (operations.Runner: one Execution per run, its
time, attempts and what ran), and the comparison is built from those records only, never from anyone's words.

- `select(hub, operation, names)`: the models named, as the operation's implementations, bounded to MIN_MODELS to
  MAX_MODELS that can run here; the unknown and the unrunnable said apart.
- `arm(impl, result)` / `failed_arm(impl, why)`: one model's run as the record says it (what ran, its execution, time,
  whether it was a reuse, cost and its basis).
- `agreement(arms, members)`: for classifying, agreement between every pair (the share of the cells both named where they
  gave the same class, and Cohen's kappa), the cells every model named alike, each cell's most-said class, and each
  model's counts per class. Agreement is never accuracy (ADR 0020): no model is ranked right.
- `build(operation, arms, ...)`: the comparison record, with one table (a row per model: what it found, counts per
  class, agreement, time, cost) kept as a result the measurements table reads (tables.result_table, its sha256), so it
  is shown with the placement vocabulary (show_measurements: new-tab, beside, window...).
- `words(record)`: the record in plain facts for a model to judge and say.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

MIN_MODELS, MAX_MODELS = 2, 6
OPERATIONS = ("outline", "cells", "classify")
FORMAT = "hooke-model-comparison-v1"
NOT_ACCURACY = "Agreement between models is not accuracy (ADR 0020): none is ranked right."


# --- which models ------------------------------------------------------------------------------------------------
@dataclass
class Selection:
    impls: list = field(default_factory=list)  # distinct, runnable here, at most MAX_MODELS, in the order named
    unknown: list[str] = field(default_factory=list)  # names that mean nothing for the operation
    cannot: list[str] = field(default_factory=list)  # "X can't run here (why)"
    left_out: list[str] = field(default_factory=list)  # runnable, past MAX_MODELS

    def said(self) -> str:
        """What was not compared, and why, in a clause ("" when everything named was)."""
        parts = list(self.cannot)
        if self.unknown:
            parts.append(f"not known here: {'; '.join(self.unknown)}")
        if self.left_out:
            parts.append(f"left out ({MAX_MODELS} at once at most): {', '.join(self.left_out)}")
        return "; ".join(parts)


def select(hub, operation: str, named, *, pick=None) -> Selection:
    """The implementations `named` means for `operation` (each once, in order), those that can run here first-come up to
    MAX_MODELS. `pick(hub, op, name)` resolves one name (the brain's `choosing.pick`, which also asks about a family's
    default that can't run); by default names.resolve."""
    from . import names
    from .brain.registry import ToolFailed
    from .operations import OPERATIONS, availability

    op = OPERATIONS[operation]
    out, seen = Selection(), []
    for name in list(named or []):
        try:
            if pick is not None:
                impl = pick(hub, op, name)
            else:
                meaning = names.resolve(operation, name)
                impl = op.impl(meaning.id) if meaning is not None else None
        except ToolFailed:
            impl = None
        if impl is None:
            out.unknown.append(str(name))
            continue
        if impl not in seen:
            seen.append(impl)  # the same model named twice counts once
    for impl in seen:
        why = availability(hub, impl)
        if why:
            out.cannot.append(f"{impl.label} can't run here ({why})")
        elif len(out.impls) < MAX_MODELS:
            out.impls.append(impl)
        else:
            out.left_out.append(impl.label)
    return out


# --- one model's run, from its record ----------------------------------------------------------------------------
def arm(impl, result, **extra) -> dict:
    """One model's run as the runner recorded it: what was asked, what ran (a fallback by what it fell back to), its
    execution, when, its time (None for a reuse: not a run), and its cost with the basis (a model on this Mac: 0)."""
    execution = result.execution
    reused = bool(getattr(result.use, "reused", False))
    output = result.output if isinstance(result.output, dict) else {}
    local = impl.worker != "cloud"
    cost = 0.0 if local else output.get("costUsd")
    basis = "runs on this Mac" if local else output.get("costBasis") or "not reported for this model's runs"
    return {"impl": impl.id, "label": impl.label, "executed": execution.executed or impl.id, "execution": execution.id,
            "at": execution.at, "ms": None if reused else round(execution.ms, 1), "reused": reused,
            "runs": "this Mac" if local else "cloud", "costUsd": None if reused else cost,
            "costBasis": "a reuse: nothing new was spent" if reused else basis,
            "fallbackFrom": execution.made_by().get("fallbackFrom"), **extra}


def failed_arm(impl, why: str) -> dict:
    return {"impl": impl.id, "label": impl.label, "failed": str(why)[:300]}


def ran_words(a: dict) -> str:
    """What produced an arm's result, by name: the model asked for, or "X → Y, its fallback" (ADR 0011)."""
    from .operations import label_of

    if a.get("executed") and a["executed"] != a["impl"]:
        return f"{a['label']} → {label_of(a['executed'])}, its fallback"
    return a["label"]


# --- classifying: agreement from the labels ------------------------------------------------------------------------
def _decided(label) -> bool:
    from .classify import UNSURE

    return isinstance(label, str) and bool(label) and label != UNSURE


def kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Cohen's kappa of two raters' labels for the same items (None when chance agreement is total or no items)."""
    n = len(pairs)
    if not n:
        return None
    observed = sum(a == b for a, b in pairs) / n
    left, right = {}, {}
    for a, b in pairs:
        left[a] = left.get(a, 0) + 1
        right[b] = right.get(b, 0) + 1
    chance = sum(left[k] * right.get(k, 0) for k in left) / (n * n)
    return None if chance >= 1 else round((observed - chance) / (1 - chance), 3)


def agreement(arms: list[dict], members: list[str]) -> dict:
    """What the models' labels say together, for the members they were all given: each model's counts per label, every
    pair's agreement over the cells both named with a class (not "unsure"), the cells every model that ran named alike,
    and each cell's most-said class with how many said it."""
    ran = [a for a in arms if not a.get("failed")]
    labels = {m: {a["impl"]: (a.get("labels") or {}).get(m, {}).get("label") for a in ran} for m in members}
    counts = {}
    for a in ran:
        tally: dict[str, int] = {}
        for m in members:
            label = labels[m].get(a["impl"])
            if label:
                tally[label] = tally.get(label, 0) + 1
        counts[a["impl"]] = dict(sorted(tally.items(), key=lambda t: (-t[1], t[0])))
    pairs = []
    for i in range(len(ran)):
        for j in range(i + 1, len(ran)):
            a, b = ran[i], ran[j]
            both = [(labels[m][a["impl"]], labels[m][b["impl"]]) for m in members
                    if _decided(labels[m][a["impl"]]) and _decided(labels[m][b["impl"]])]
            same = sum(x == y for x, y in both)
            pairs.append({"a": a["impl"], "b": b["impl"], "both": len(both), "same": same,
                          "agreement": round(same / len(both), 3) if both else None, "kappa": kappa(both)})
    every = [m for m in members if len(ran) >= 2 and all(_decided(labels[m][a["impl"]]) for a in ran)]
    alike = [m for m in every if len({labels[m][a["impl"]] for a in ran}) == 1]
    majority = {}
    for m in members:
        said = [labels[m][a["impl"]] for a in ran if _decided(labels[m][a["impl"]])]
        if said:
            best = max(sorted(set(said)), key=said.count)
            majority[m] = {"label": best, "agree": said.count(best), "of": len(ran)}
    return {"labels": labels, "counts": counts, "pairs": pairs, "everyNamed": len(every), "allAgree": len(alike),
            "alike": alike, "majority": majority, "members": list(members)}


def agreement_of(impl: str, pairs: list[dict]) -> float | None:
    """A model's mean agreement with the others (over the pairs with any cell both named)."""
    values = [p["agreement"] for p in pairs if impl in (p["a"], p["b"]) and p["agreement"] is not None]
    return round(sum(values) / len(values), 3) if values else None


# --- the record and its table --------------------------------------------------------------------------------------
def _cost_cell(a: dict):
    return None if a.get("failed") else a.get("costUsd")


def _seconds(a: dict):
    return None if a.get("failed") or a.get("ms") is None else round(a["ms"] / 1000, 2)


def table(operation: str, arms: list[dict], summary: dict, *, um_per_px: float | None = None) -> tuple[list, list]:
    """One row per model: what it found, its agreement with the others, its time and cost (columns and rows as the
    measurements table reads a kept table: tables.result_table)."""
    labels = {a["impl"]: a["label"] for a in arms}
    others = [a for a in arms if not a.get("failed")]
    head = [{"name": "Model"}, {"name": "What ran", "definition": "the model asked for, or the fallback that ran"}]
    tail = [{"name": "Time", "unit": "s", "definition": "the run's own time; empty for a reuse or a failure"},
            {"name": "Cost", "unit": "USD", "definition": "reported tokens at the list price; 0 on this Mac; empty "
                                                          "when not reported"},
            {"name": "Reused", "definition": "the result was an earlier run's, not a new one"},
            {"name": "Failed", "definition": "why it gave nothing, in its own words"}]
    rows = []
    if operation == "classify":
        from .classify import NONE_OF_THESE, UNSURE

        order = list(summary.get("classes") or [])
        totals: dict[str, int] = {}
        for c in summary["counts"].values():
            for label, n in c.items():
                totals[label] = totals.get(label, 0) + n
        extra = sorted((l for l in totals if l not in order and l not in (UNSURE, NONE_OF_THESE)),
                       key=lambda l: (-totals[l], l))
        room = max(0, 10 - len(order))  # a model's own words beyond the classes: the most said get columns
        shown, rest = order + extra[:room], set(extra[room:])
        none = bool(order) or NONE_OF_THESE in totals
        middle = ([{"name": "Named", "definition": "cells it gave a class (not unsure)"},
                   {"name": "Unsure", "definition": "cells it said it could not tell"}]
                  + [{"name": c, "definition": "cells it gave this class"} for c in shown]
                  + ([{"name": NONE_OF_THESE, "definition": "cells it said fit none of the classes"}] if none else [])
                  + ([{"name": "Other words", "definition": "cells it named with words outside the columns"}] if rest else [])
                  + [{"name": "Agrees with the others", "unit": "%",
                      "definition": "mean share of the cells both named where it gave the same class; not accuracy"}])
        for a in arms:
            c = summary["counts"].get(a["impl"], {})
            row = [a["label"], None if a.get("failed") else ran_words(a)]
            if a.get("failed"):
                row += [None] * len(middle)
            else:
                agree = agreement_of(a["impl"], summary["pairs"])
                row += ([sum(n for l, n in c.items() if l != UNSURE), c.get(UNSURE, 0)] + [c.get(k, 0) for k in shown]
                        + ([c.get(NONE_OF_THESE, 0)] if none else [])
                        + ([sum(n for l, n in c.items() if l in rest)] if rest else [])
                        + [None if agree is None else round(agree * 100, 1)])
            rows.append(row + [_seconds(a), _cost_cell(a), bool(a.get("reused")), a.get("failed")])
        return head + middle + tail, rows
    if operation == "outline":
        middle = [{"name": "Area", "unit": "px²", "definition": "the outline's area on the compared view"}]
        if um_per_px:
            middle.append({"name": "Area", "unit": "µm²", "definition": "the same area at the view's scale"})
        middle += [{"name": f"Overlap with {labels[o['impl']]}", "unit": "IoU",
                    "definition": "intersection over union of the two outlines"} for o in others]
        for a in arms:
            row = [a["label"], None if a.get("failed") else ran_words(a)]
            if a.get("failed"):
                row += [None] * len(middle)
            else:
                row.append(a.get("areaPx"))
                if um_per_px:
                    row.append(round(a["areaPx"] * um_per_px * um_per_px, 1) if a.get("areaPx") is not None else None)
                row += [None if o["impl"] == a["impl"] else _pair_value(summary, a["impl"], o["impl"], "iou") for o in others]
            rows.append(row + [_seconds(a), _cost_cell(a), bool(a.get("reused")), a.get("failed")])
        return head + middle + tail, rows
    middle = [{"name": "Cells", "definition": "instances it segmented in the compared view"}]
    middle += [{"name": f"Found by {labels[o['impl']]} too", "definition": "its cells whose box another model's "
                "cell matches (at least half alike)"} for o in others]
    for a in arms:
        row = [a["label"], None if a.get("failed") else ran_words(a)]
        if a.get("failed"):
            row += [None] * len(middle)
        else:
            row.append(a.get("count"))
            row += [None if o["impl"] == a["impl"] else _pair_value(summary, a["impl"], o["impl"], "matched") for o in others]
        rows.append(row + [_seconds(a), _cost_cell(a), bool(a.get("reused")), a.get("failed")])
    return head + middle + tail, rows


def _pair_value(summary: dict, a: str, b: str, key: str):
    for p in summary.get("pairs") or ():
        if {p.get("a"), p.get("b")} == {a, b}:
            return p.get(key)
    return None


def build(operation: str, arms: list[dict], *, on: dict, policy: str = "raw", summary: dict | None = None,
          um_per_px: float | None = None) -> dict:
    """The comparison record: what was compared on, each model's run, what they say together (summary), totals, and
    its one table; `values(record)` is what a result entry keeps."""
    summary = dict(summary or {})
    columns, rows = table(operation, arms, summary, um_per_px=um_per_px)
    ran = [a for a in arms if not a.get("failed")]
    costs = [a.get("costUsd") for a in ran if not a.get("reused")]
    totals = {"models": len(arms), "ran": len(ran), "failed": len(arms) - len(ran),
              "seconds": round(sum(a["ms"] for a in ran if a.get("ms") is not None) / 1000, 2),
              "costUsd": round(sum(c for c in costs if c is not None), 6),
              "costComplete": all(c is not None for c in costs)}
    return {"format": FORMAT, "operation": operation, "policy": policy, "on": dict(on), "arms": arms,
            "summary": summary, "totals": totals, "columns": columns, "rows": rows, "says": NOT_ACCURACY}


def values(record: dict) -> dict:
    """What a result entry keeps of a comparison: the record, and its table bound to its sha256 (tables.result_table),
    so the measurements table can show it and anyone reading it gets the same numbers."""
    from .tables import kept_table_digest

    body = {"columns": record["columns"], "rows": record["rows"]}
    comparison = {k: v for k, v in record.items() if k not in ("columns", "rows")}
    return {"comparison": comparison, **body,
            "producer": {"implementation": "model-comparison", "version": FORMAT, "sha256": kept_table_digest(body)}}


def title(record: dict) -> str:
    what = {"outline": "outlines", "cells": "cell finders", "classify": "classifiers"}[record["operation"]]
    return f"{len(record['arms'])} {what} compared"


def words(record: dict) -> str:
    """The record as facts in plain words: each model's run, then what they say together, then the totals."""
    from .classify import UNSURE, said_labels

    op, summary, parts = record["operation"], record["summary"], []
    for a in record["arms"]:
        if a.get("failed"):
            parts.append(f"{a['label']} failed ({a['failed']})")
            continue
        how = ("reused from an earlier run (its time not counted)" if a.get("reused") else
               f"in {a['ms'] / 1000:.1f} s" if a.get("ms") is not None else "just now")
        cost = (f", ${a['costUsd']:.4f}" if isinstance(a.get("costUsd"), float) and a["costUsd"] > 0 else
                ", nothing spent (this Mac)" if a.get("costUsd") == 0 and a.get("runs") == "this Mac" else
                "" if a.get("reused") else ", its cost not reported")
        if op == "classify":
            unsure = (summary["counts"].get(a["impl"]) or {}).get(UNSURE, 0)
            named = {m: {"label": l} for m, l in ((m, row.get(a["impl"])) for m, row in summary["labels"].items()) if l}
            parts.append(f"{ran_words(a)}: {said_labels(named) or 'nothing named'}"
                         + (f" ({unsure} unsure)" if unsure else "") + f", {how}{cost}")
        elif op == "outline":
            parts.append(f"{ran_words(a)} {a.get('areaPx', 0):,.0f} px², {how}{cost}")
        else:
            parts.append(f"{ran_words(a)}: {a.get('count')} cells, {how}{cost}")
    out = "; ".join(parts)
    labels = {a["impl"]: a["label"] for a in record["arms"]}
    if op == "classify":
        n = len(summary.get("members") or ())
        together = [f"of {n} cells, {summary['allAgree']} were named alike by every model that ran "
                    f"({summary['everyNamed']} named by all of them)"]
        for p in summary["pairs"]:
            if p["agreement"] is None:
                together.append(f"{labels[p['a']]} and {labels[p['b']]}: no cell both named")
                continue
            together.append(f"{labels[p['a']]} and {labels[p['b']]} gave the same class to {p['same']} of {p['both']} "
                            f"cells both named ({p['agreement'] * 100:.0f}%"
                            + (f", kappa {p['kappa']:.2f}" if p["kappa"] is not None else "") + ")")
        out += "; " + "; ".join(together)
        if summary.get("vocabulary") == "open":
            out += "; no classes were given, so each named them in its own words, compared word for word"
    elif op == "outline":
        out += "".join(f"; {labels[p['a']]} and {labels[p['b']]} overlap {p['iou'] * 100:.0f}% (IoU)"
                       for p in summary.get("pairs") or () if p.get("iou") is not None)
    else:
        out += "".join(f"; {p['matched']} found by both {labels[p['a']]} and {labels[p['b']]}"
                       for p in summary.get("pairs") or ())
    t = record["totals"]
    out += (f"; in all {t['seconds']:.1f} s of runs and ${t['costUsd']:.4f}"
            + ("" if t["costComplete"] else " (some costs not reported)") + f". {record['says']}")
    return out


async def compare_classes(hub, population, impls: list, *, classes=None, origin: str = "compare", policy: str = "raw",
                          made_for: str | None = None, priority: str | None = None, limit: int | None = None) -> dict:
    """Every model in `impls` names the same members of `population` from the same classes (classify.classes_for: given,
    else the subject's list, else open naming), each through the runner, at once; the comparison record of their runs.
    A model that fails is an arm that says why; the others are still compared."""
    import asyncio

    from . import classify
    from .brain.registry import ToolFailed

    names, vocabulary, template, calibration = classify.classes_for(hub, classes)
    members = classify.chosen_members(population, limit or classify.MAX_MEMBERS)
    if not members:
        raise ToolFailed(f"{population.title} has no cells to classify", next="outline the cells first")

    async def one(impl) -> dict:
        try:
            result = await classify.run(hub, population, impl.id, classes=names, vocabulary=vocabulary, members=members,
                                        template=template, calibration=calibration, origin=origin, policy=policy,
                                        made_for=made_for, priority=priority)
        except ToolFailed as exc:  # an Unavailable too: said with the others, never the whole comparison's failure
            return failed_arm(impl, exc)
        out = result.output
        return arm(impl, result, labels=out.get("labels") or {}, unanswered=len(out.get("unanswered") or ()),
                   calls=out.get("calls"))

    arms = list(await asyncio.gather(*(one(impl) for impl in impls)))
    summary = agreement(arms, [m.id for m in members])
    summary.update(classes=names, vocabulary=vocabulary,
                   **({"calibration": calibration} if calibration else {}))
    for a in arms:
        a.pop("labels", None)  # kept once, by member, in the summary
    return build("classify", arms, policy=policy, summary=summary,
                 on={"population": population.id, "revision": population.revision, "title": population.title,
                     "capture": population.capture_id, "members": len(members), "of": len(population.live())})


def headline(record: dict) -> str:
    """The record in one line (a researcher's card): how many ran, how far they agree, the totals; the numbers are in
    the record itself."""
    t, summary = record["totals"], record["summary"]
    ran = f"{t['ran']} of {t['models']} models ran" + (f" ({t['failed']} failed)" if t["failed"] else "")
    if record["operation"] == "classify":
        shares = [p["agreement"] for p in summary.get("pairs") or () if p.get("agreement") is not None]
        spread = (f"; pairs agreed on {min(shares) * 100:.0f}% to {max(shares) * 100:.0f}% of the cells both named"
                  if shares else "")
        ran += (f"; of {len(summary.get('members') or ())} cells, {summary['allAgree']} were named alike by every model "
                f"that ran{spread}")
    return ran + f"; {t['seconds']:.1f} s of runs, ${t['costUsd']:.4f}" + ("" if t["costComplete"] else " (some not reported)")


def digest(record: dict) -> str:
    """A stable identity of what was compared and what each run was (for tests and receipts)."""
    body = {"operation": record["operation"], "on": record["on"],
            "arms": [{k: a.get(k) for k in ("impl", "executed", "execution", "failed")} for a in record["arms"]]}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
