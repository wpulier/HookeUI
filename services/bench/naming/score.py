"""Scoring names against labelled pictures (bench/naming): exact and coarse matches, abstention,
calibration and how much the context steered the name. Pure functions: no models, no files.

A name is matched the way the app matches labels (orchestrator/labels.py: label_key, SYNONYMS),
plus a small vocabulary per labelled set: the names a class goes by, and the coarser group it
belongs to ("neutrophil" is a white blood cell is a blood cell). A name is right at the fine
level if it names the class; right at the coarse level if it names the class or its group.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from orchestrator.labels import label_key

# Per labelled set: class -> other names it goes by, and class -> its coarse group; plus the
# words that name each group. Classes not listed match by their own name only.
VOCAB = {
    "acevedo_et_al_2020": {
        "subject": "a blood smear (Giemsa stain)",
        "wrong_subject": "a leaf epidermis",
        "names": {
            "neutrophil": ["neutrophil", "segmented neutrophil", "band neutrophil", "band cell", "polymorphonuclear"],
            "eosinophil": ["eosinophil"],
            "basophil": ["basophil"],
            "lymphocyte": ["lymphocyte"],
            "monocyte": ["monocyte"],
            "ig": ["immature granulocyte", "myelocyte", "metamyelocyte", "promyelocyte"],
            "immature granulocyte": ["immature granulocyte", "myelocyte", "metamyelocyte", "promyelocyte"],
            "erythroblast": ["erythroblast", "nucleated red blood cell", "normoblast", "nrbc"],
            "platelet": ["platelet", "thrombocyte", "platelet clump"],
        },
        "group": {"neutrophil": "white blood cell", "eosinophil": "white blood cell", "basophil": "white blood cell",
                  "lymphocyte": "white blood cell", "monocyte": "white blood cell", "ig": "white blood cell",
                  "immature granulocyte": "white blood cell", "erythroblast": "blood cell", "platelet": "blood cell"},
        "group_words": {"white blood cell": ["white blood cell", "leukocyte", "granulocyte", "wbc", "neutrophil",
                                             "eosinophil", "basophil", "lymphocyte", "monocyte", "myelocyte"],
                        "blood cell": ["blood cell", "red blood cell", "white blood cell", "leukocyte", "platelet",
                                       "erythrocyte", "erythroblast", "neutrophil", "eosinophil", "basophil",
                                       "lymphocyte", "monocyte", "granulocyte", "thrombocyte"]},
    },
    "icpr2020_pollen": {
        "subject": "pollen grains",
        "wrong_subject": "a blood smear",
        "names": {"corylus avellana": ["corylus", "hazel"], "alnus": ["alnus", "alder"], "debris": ["debris", "dust", "artifact"]},
        "group": {},  # every class not listed below is a pollen grain
        "default_group": "pollen grain",
        "group_words": {"pollen grain": ["pollen"]},
    },
    "hussain_et_al_2019": {
        "subject": "a Pap smear (cervical cytology)",
        "wrong_subject": "an onion epidermis",
        # Its classes are diagnostic grades (NILM, LSIL, HSIL, SCC), not names a person looking
        # would give; the app is not a diagnostic tool, so only the coarse name is scored.
        "grades": True,
        "names": {},
        "group": {},
        "default_group": "squamous epithelial cell",
        "group_words": {"squamous epithelial cell": ["squamous", "epithelial", "cervical cell", "parabasal",
                                                     "superficial cell", "intermediate cell", "metaplastic",
                                                     "koilocyte", "dyskaryotic"]},
    },
}

ABSTAIN = re.compile(r"\b(unsure|unclear|uncertain|not sure|can't tell|cannot tell|can not tell|unknown|unidentified|"
                     r"indeterminate|no idea|blank|empty|nothing)\b", re.I)


def _key(text: str) -> str:
    return label_key(text or "").strip()


def _contains(name: str, phrase: str) -> bool:
    """`phrase` is named in `name`, by whole words after the app's label normalisation."""
    have, want = _key(name).split(), _key(phrase).split()
    if not want:
        return False
    return any(have[i : i + len(want)] == want for i in range(len(have) - len(want) + 1)) or _key(name) == _key(phrase)


def vocab(dataset: str) -> dict:
    return VOCAB.get(dataset, {"names": {}, "group": {}, "group_words": {}})


def truth_names(dataset: str, label: str) -> list[str]:
    v = vocab(dataset)
    key = label.lower().strip()
    for cls, names in v["names"].items():
        if key == cls or key.startswith(cls):
            return [label, *names]
    return [label]


def truth_group(dataset: str, label: str) -> str | None:
    v = vocab(dataset)
    key = label.lower().strip()
    for cls, group in v["group"].items():
        if key == cls or key.startswith(cls):
            return group
    if key in ("debris",):
        return None
    return v.get("default_group")


def abstained(name: str) -> bool:
    return not (name or "").strip() or bool(ABSTAIN.search(name))


def judge(dataset: str, label: str, name: str) -> dict:
    """One answer against its truth: {"abstained", "fine", "coarse"}."""
    graded = bool(vocab(dataset).get("grades"))
    if abstained(name):
        return {"abstained": True, "fine": None if graded else False, "coarse": False}
    fine = any(_contains(name, n) for n in truth_names(dataset, label))
    group = truth_group(dataset, label)
    words = vocab(dataset)["group_words"].get(group, []) if group else []
    coarse = fine or any(_contains(name, w) for w in words)
    return {"abstained": False, "fine": None if graded else fine, "coarse": coarse}


def ece(pairs: list[tuple[float, bool]], bins: int = 10) -> float | None:
    """Expected calibration error of stated confidence against being right (0 is perfect)."""
    pairs = [(min(1.0, max(0.0, c)), ok) for c, ok in pairs if isinstance(c, (int, float))]
    if not pairs:
        return None
    total, error = len(pairs), 0.0
    for b in range(bins):
        low, high = b / bins, (b + 1) / bins
        inside = [(c, ok) for c, ok in pairs if low <= c < high or (b == bins - 1 and c == 1.0)]
        if inside:
            confidence = sum(c for c, _ in inside) / len(inside)
            accuracy = sum(1 for _, ok in inside if ok) / len(inside)
            error += len(inside) / total * abs(confidence - accuracy)
    return round(error, 4)


def summarise(results: list[dict]) -> dict:
    """Results are rows {dataset, label, image, condition, option, repeat, name, confidence, ms}.
    Returns per option and condition: accuracy, abstention, ECE, latency, per-class accuracy and
    confusions; and per option the context leak (how often the name changed with the context)."""
    out: dict = {}
    by = defaultdict(list)
    for r in results:
        by[(r["option"], r["condition"])].append(r)
    rate = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    for (option, condition), rows in sorted(by.items()):
        judged = [(r, judge(r["dataset"], r["label"], r.get("name") or "")) for r in rows if not r.get("error")]
        n = len(judged)
        if not n:
            continue
        per_class = defaultdict(list)
        confusions = defaultdict(Counter)
        by_set = defaultdict(list)
        for r, j in judged:
            right = j["fine"] if j["fine"] is not None else j["coarse"]  # graded sets: the coarse name
            per_class[r["label"]].append(right)
            by_set[r["dataset"]].append(j)
            if not right:
                confusions[r["label"]][_key(r.get("name") or "") or "(none)"] += 1
        fine = [j["fine"] for _, j in judged if j["fine"] is not None]
        ms = sorted(r["ms"] for r, _ in judged if isinstance(r.get("ms"), (int, float)))
        out.setdefault(option, {})[condition] = {
            "n": n,
            "errors": sum(1 for r in rows if r.get("error")),
            "fine": rate(fine),  # over sets with names (graded sets left out)
            "coarse": rate([j["coarse"] for _, j in judged]),
            "abstained": rate([j["abstained"] for _, j in judged]),
            "ece": ece([(r.get("confidence"), j["fine"] if j["fine"] is not None else j["coarse"]) for r, j in judged]),
            "p50Ms": ms[len(ms) // 2] if ms else None,
            # What the app showed (W1-3b): how often each status was right, and how often it was used.
            # "specific" is judged too: was the name it would have given right, when it held back?
            "byStatus": {status: {"share": round(len(rs) / n, 3),
                                  "right": rate([(j["fine"] if j["fine"] is not None else j["coarse"]) for r, j in rs]),
                                  "specificRight": rate([(sj["fine"] if sj["fine"] is not None else sj["coarse"])
                                                         for sj in (judge(r["dataset"], r["label"], r.get("specific") or "")
                                                                    for r, _ in rs)])}
                         for status, rs in _group(judged, lambda rj: rj[0].get("status")).items() if status},
            "bySet": {name: {"n": len(js), "fine": rate([j["fine"] for j in js if j["fine"] is not None]),
                             "coarse": rate([j["coarse"] for j in js])} for name, js in sorted(by_set.items())},
            "perClass": {k: rate(v) for k, v in sorted(per_class.items())},
            "confusions": {k: dict(c.most_common(3)) for k, c in sorted(confusions.items())},
        }
    for option in out:
        out[option]["contextLeak"] = context_leak([r for r in results if r["option"] == option])
        out[option]["consistency"] = consistency([r for r in results if r["option"] == option])
    return out


def context_leak(rows: list[dict]) -> dict:
    """For each picture, did the name change when the context changed? Compared with the
    right context: no context, the wrong context. Plus: how often a grey picture with the
    right context still got the right name (the name came from the words, not the pixels)."""
    names = defaultdict(dict)
    for r in rows:
        if r.get("repeat", 0) == 0 and not r.get("error"):
            names[r["image"]][r["condition"]] = (r["dataset"], r["label"], r.get("name") or "")
    changed = {"none": [], "wrong": []}
    grey_named = []
    for image, by_condition in names.items():
        right = by_condition.get("right")
        if right is None:
            continue
        for condition in changed:
            if condition in by_condition:
                changed[condition].append(_key(by_condition[condition][2]) != _key(right[2]))
        if "grey" in by_condition:
            dataset, label, name = by_condition["grey"]
            grey_named.append(judge(dataset, label, name)["coarse"])
    rate = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    return {"changedWithoutContext": rate(changed["none"]), "changedWithWrongContext": rate(changed["wrong"]),
            "greyImageNamedFromContext": rate(grey_named), "pictures": len(names)}


def consistency(rows: list[dict]) -> float | None:
    """How often repeats of the same picture and condition gave the same name."""
    groups = defaultdict(list)
    for r in rows:
        if not r.get("error"):
            groups[(r["image"], r["condition"])].append(_key(r.get("name") or ""))
    repeated = [g for g in groups.values() if len(g) > 1]
    if not repeated:
        return None
    return round(sum(1 for g in repeated if len(set(g)) == 1) / len(repeated), 3)


def _group(items, key) -> dict:
    out = defaultdict(list)
    for item in items:
        out[key(item)].append(item)
    return out
