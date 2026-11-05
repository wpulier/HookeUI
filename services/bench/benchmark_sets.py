"""Benchmark sets: results in the one shape anyone's comparison takes (contract/benchmarks/benchmark-set.schema.json).

A set is one comparison of ways to do one job, run at one time: its arms (models, options or methods), the metrics with
which way is better, each arm's numbers, optionally each case's, and what it cost, its caveats and the decision it
informed. Hooke's own sets are in docs/bench/sets/ (docs/bench/README.md says what each measures); a user's Hooke or an
agent writes the same shape, so a website can list them side by side.

  cd services && .venv/bin/python -m bench.benchmark_sets check [FILE ...]   # every set in docs/bench/sets by default

`fast_bench_set` turns `bench.fast_bench` rows (results/fast-bench.json) into a set's results and cases.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from orchestrator.door import schema
from orchestrator.paths import REPO

FOLDER = REPO / "contract" / "benchmarks"
SETS = REPO / "docs" / "bench" / "sets"
NAME = "benchmark-set"


def problems(value) -> list[str]:
    """Every way a set breaks the schema or does not hold together; [] when it is sound."""
    found = schema.problems(value, NAME, FOLDER)
    if found or not isinstance(value, dict):
        return found
    arms = [a["id"] for a in value["arms"]]
    metrics = [m["id"] for m in value["metrics"]]
    for kind, ids in (("arm", arms), ("metric", metrics)):
        found += [f"{kind} {i!r} is listed twice" for i, n in Counter(ids).items() if n > 1]
    resulted = [r["arm"] for r in value["results"]]
    found += [f"results name an unknown arm {a!r}" for a in resulted if a not in arms]
    found += [f"arm {a!r} has no results" for a in arms if a not in resulted]

    def values(where: str, items) -> None:
        for v in items:
            if v["metric"] not in metrics:
                found.append(f"{where} uses an unknown metric {v['metric']!r}")
            if v.get("outOf") is not None and v["value"] is not None and v["value"] > v["outOf"]:
                found.append(f"{where}: {v['metric']} {v['value']} is more than its {v['outOf']}")

    for r in value["results"]:
        values(f"results for {r['arm']}", r["values"])
    cases = [c["id"] for c in value.get("cases", [])]
    found += [f"case {i!r} is listed twice" for i, n in Counter(cases).items() if n > 1]
    for case in value.get("cases", []):
        for r in case["results"]:
            if r["arm"] not in arms:
                found.append(f"case {case['id']} names an unknown arm {r['arm']!r}")
            if "right" in r and "tries" in r and r["right"] > r["tries"]:
                found.append(f"case {case['id']}: {r['arm']} right {r['right']} of {r['tries']} tries")
            values(f"case {case['id']} for {r['arm']}", r.get("values", []))
    for link in value.get("links", []):
        href = link.get("href", "")
        if link["kind"] in ("write-up", "code") and href and "://" not in href and not (REPO / href).exists():
            found.append(f"link {href!r} is not in the repository")
    return found


def fast_bench_set(rows_by_arm: dict[str, list[dict]], cost_by_arm: dict[str, dict] | None = None,
                   timeout_ms: float = 15000.0) -> tuple[list[dict], list[dict]]:
    """(results, cases) from `bench.fast_bench` rows per arm. The metrics they use: right, p50Ms, p90Ms, p99Ms, maxMs,
    over3s, overTimeout and, with `cost_by_arm` (provider-reported {"calls", "in", "usd"} per arm), inputTokensPerCall,
    costPerCallUsd and runUsd. Percentiles as the bench computes them (numpy)."""
    import numpy as np

    results, by_case = [], {}
    for arm, rows in rows_by_arm.items():
        ms = [r["ms"] for r in rows]
        vals = [{"metric": "right", "value": sum(bool(r["right"]) for r in rows), "outOf": len(rows)},
                {"metric": "p50Ms", "value": round(float(np.median(ms)))},
                {"metric": "p90Ms", "value": round(float(np.percentile(ms, 90)))},
                {"metric": "p99Ms", "value": round(float(np.percentile(ms, 99)))},
                {"metric": "maxMs", "value": max(ms)},
                {"metric": "over3s", "value": sum(m > 3000 for m in ms), "outOf": len(ms)},
                {"metric": "overTimeout", "value": sum(m > timeout_ms for m in ms), "outOf": len(ms)}]
        cost = (cost_by_arm or {}).get(arm)
        if cost and cost.get("calls"):
            vals += [{"metric": "inputTokensPerCall", "value": round(cost["in"] / cost["calls"])},
                     {"metric": "costPerCallUsd", "value": round(cost["usd"] / cost["calls"], 4)},
                     {"metric": "runUsd", "value": round(cost["usd"], 2)}]
        results.append({"arm": arm, "values": vals})
        for r in rows:
            case = by_case.setdefault(r["text"], {"expected": r["expected"], "arms": {}})
            case["arms"].setdefault(arm, []).append(r)
    cases = []
    for n, (text, case) in enumerate(by_case.items(), 1):
        expected = case["expected"]
        out = {"id": f"c{n:02d}", "text": text,
               "expected": " or ".join(expected) if isinstance(expected, list) else str(expected), "results": []}
        for arm, rows in case["arms"].items():
            r = {"arm": arm, "right": sum(bool(x["right"]) for x in rows), "tries": len(rows)}
            missed = [x for x in rows if not x["right"]]
            if missed:
                did = Counter("a look" if x["needs_look"] else (" + ".join(x["used"]) or "no action") for x in missed)
                r["did"] = "; ".join(f"{d} ({k}x)" if k > 1 else d for d, k in did.most_common())[:300]
            out["results"].append(r)
        cases.append(out)
    return results, cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="check sets against the schema and themselves").add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    files = [Path(f) for f in args.files] or sorted(SETS.glob("*.json"))
    bad = 0
    for f in files:
        found = problems(json.loads(f.read_text()))
        bad += bool(found)
        print(f"{'ok ' if not found else 'BAD'} {f.name}" + "".join(f"\n    {p}" for p in found))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
