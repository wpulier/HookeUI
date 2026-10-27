"""Will's labels against the judge, and what the frozen rule decides (docs/bench/outline-fit.md).

The labels are the review page's database (collection "labels", one document per outline: {"verdict": "fits"
(Nailed it) | "kinda" | "doesnt" (Missed) | "unsure" (Can't tell)}), saved as JSON files by ArtifactData (out_dir) into a folder:

  cd services && .venv/bin/python -m bench.outline_fit_review <folder with labels/*.json>
  cd services && .venv/bin/python -m bench.outline_fit_review --round 2 <folder>   (round 2's rule)
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "outline-fit"
ARMS = ("edgetam", "sam2.1-tiny", "micro-sam-lm", "sam3.1-local", "the box itself")


def labels(folder: Path) -> dict[int, str]:
    out = {}
    for f in folder.rglob("*.json"):
        doc = json.loads(f.read_text())
        body = doc.get("data", doc)
        verdict = body.get("verdict")
        if f.stem.startswith("o") and f.stem[1:].isdigit() and verdict in ("fits", "kinda", "doesnt", "unsure"):
            out[int(f.stem[1:])] = verdict
    return out


def kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    n = len(pairs)
    if not n:
        return None
    agree = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    chance = pa * pb + (1 - pa) * (1 - pb)
    return None if chance == 1 else round((agree - chance) / (1 - chance), 2)


def main() -> None:
    will = labels(Path(sys.argv[1]))
    rows = {r["id"]: r for r in map(json.loads, (OUT / "rows.jsonl").open())}
    told = [(rows[i], v) for i, v in will.items() if i in rows]
    could = [(r, v) for r, v in told if v != "unsure"]
    pairs = [(r["fits"] == "yes", v in ("fits", "kinda")) for r, v in could]  # usable: Nailed or Kinda
    strict = [(r["fits"] == "yes", v == "fits") for r, v in could]  # Nailed alone
    matrix = Counter((("judge fits" if j else "judge doesn't"), ("Will fits" if w else "Will doesn't")) for j, w in pairs)
    agreement = sum(a == b for a, b in pairs) / len(pairs) if pairs else 0.0
    k = kappa(pairs)
    stands = agreement >= 0.8 and (k or 0) >= 0.6

    # Every target's verdict by arm, the judge's on all 54, Will's where he labelled it.
    by_target = defaultdict(dict)
    for r in rows.values():
        by_target[(r["frame"], r["label"], tuple(r["box"]))][r["arm"]] = r
    judged = {arm: sum(1 for t in by_target.values() if t.get(arm, {}).get("fits") == "yes") for arm in ARMS}
    willed = defaultdict(lambda: [0.0, 0])
    for r, v in could:
        if r["arm"] in ARMS:
            willed[r["arm"]][0] += {"fits": 1.0, "kinda": 0.5}.get(v, 0.0)
            willed[r["arm"]][1] += 1
    decision = "nothing moves: the judge did not stand (fix its prompt or model first)"
    if stands:
        base = judged["edgetam"]
        better = [a for a in ARMS if a != "edgetam" and judged[a] >= base + 4]
        ok = [a for a in better if (willed[a][0] / willed[a][1] if willed[a][1] else 1)
              >= (willed["edgetam"][0] / willed["edgetam"][1] if willed["edgetam"][1] else 0)]
        decision = (f"move the look's outline model to {max(ok, key=lambda a: judged[a])}" if ok
                    else "nothing moves: no arm beats EdgeTAM by 4 and holds on Will's labels")
    result = {"labelled": len(told), "couldTell": len(could), "unsure": len(told) - len(could),
              "matrix": {f"{a} / {b}": n for (a, b), n in sorted(matrix.items())},
              "agreement": round(agreement, 2), "kappa": k, "judgeStands": stands,
              "strict": {"agreement": round(sum(a == b for a, b in strict) / len(strict), 2) if strict else None,
                         "kappa": kappa(strict)},
              "willVerdicts": dict(Counter(v for _, v in told)),
              "judgedFitsOf54": judged, "willScoreOnHisLabels": {a: f"{f:g}/{n}" for a, (f, n) in willed.items()},
              "decision": decision}
    (OUT / "review-result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


def round_2(folder: Path) -> None:
    """Round 2 (docs/bench/outline-fit.md "Round 2", frozen before the run and the labels): the judge rates nailed, kinda
    or missed; usable is nailed or kinda. It stands at 80% agreement with Will on usable against missed and kappa 0.6.
    If it stands, a region default needs 4 more usable targets than EdgeTAM over all targets and a Will score no lower;
    a ring arm 3 more than its model's box on the ring targets and a Will score no lower."""
    out = HERE / "results" / "outline-regions"
    will = labels(folder)
    rows = {r["id"]: r for r in map(json.loads, (out / "rows.jsonl").open())}
    told = [(rows[i], v) for i, v in will.items() if i in rows]
    could = [(r, v) for r, v in told if v != "unsure"]
    usable = lambda rating: rating in ("nailed", "kinda")  # noqa: E731
    pairs = [(usable(r["rating"]), v in ("fits", "kinda")) for r, v in could]
    exact = [(r["rating"], {"fits": "nailed", "kinda": "kinda", "doesnt": "missed"}[v]) for r, v in could]
    agreement = sum(a == b for a, b in pairs) / len(pairs) if pairs else 0.0
    k = kappa(pairs)
    stands = agreement >= 0.8 and (k or 0) >= 0.6
    arms = list(dict.fromkeys(r["arm"] for r in rows.values()))
    judged = {arm: sum(1 for r in rows.values() if r["arm"] == arm and usable(r["rating"])) for arm in arms}
    rings = {arm: sum(1 for r in rows.values() if r["arm"] == arm and r["ring"] and usable(r["rating"])) for arm in arms}
    willed = defaultdict(lambda: [0.0, 0])
    for r, v in could:
        willed[r["arm"]][0] += {"fits": 1.0, "kinda": 0.5}.get(v, 0.0)
        willed[r["arm"]][1] += 1
    score = lambda arm: (willed[arm][0] / willed[arm][1]) if willed[arm][1] else None  # noqa: E731
    decisions = []
    if not stands:
        decisions.append("nothing moves: the judge did not stand; Will's labels per arm are reported as they are")
    else:
        base = score("edgetam")
        for arm in arms:
            if " as a ring" in arm or arm == "edgetam":
                continue
            if judged[arm] >= judged["edgetam"] + 4 and (score(arm) is None or base is None or score(arm) >= base):
                decisions.append(f"regions: {arm} over EdgeTAM ({judged[arm]} usable against {judged['edgetam']})")
        for arm in arms:
            if " as a ring" in arm:
                model = arm.replace(" as a ring", "")
                if rings[arm] >= rings[model] + 3 and (score(arm) is None or score(model) is None or score(arm) >= score(model)):
                    decisions.append(f"rings: {arm} over {model}'s box ({rings[arm]} usable against {rings[model]})")
        decisions = decisions or ["nothing moves: no arm cleared its margin"]
    result = {"labelled": len(told), "couldTell": len(could), "unsure": len(told) - len(could),
              "willVerdicts": dict(Counter(v for _, v in told)), "agreement": round(agreement, 2), "kappa": k,
              "exactAgreement": round(sum(a == b for a, b in exact) / len(exact), 2) if exact else None,
              "judgeStands": stands, "usableOfAll": judged, "usableOfRings": rings,
              "willScore": {a: f"{f:g}/{n}" for a, (f, n) in willed.items()}, "decisions": decisions}
    (out / "review-result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--round":
        round_2(Path(sys.argv[3])) if sys.argv[2] == "2" else main()
    else:
        main()
