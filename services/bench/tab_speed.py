"""How fast Hooke's Tab actions are, from what it logged doing them (`tab-action`, workspace_tabs.py): each action, shown
or prepared quietly, with its count, median and 95th percentile, split into the studio's round trip, the wait for the
screen to show it, and the whole. The targets (docs/architecture/on-screen.md "Quiet and shown"): a switch within
150 ms, and any single Tab action done and checked within 1 s, both at the median.

  make tab-speed                                   (the live app's log, MICROMIND_HOME)
  cd services && .venv/bin/python -m bench.tab_speed --log /tmp/vj-rt/home/logs/interactions.jsonl --since 2026-11-03
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

TARGETS_MS = {"switch": 150.0}  # an action's own median target; every other action is held to ANY_MS
ANY_MS = 1000.0
PARTS = ("studioMs", "verifyMs", "totalMs")


def percentile(values: list[float], q: float) -> float | None:
    """The q-th percentile (0-100) by nearest rank; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(q / 100 * len(ordered))))
    return ordered[rank - 1]


def read(paths: list[Path], since: str | None = None) -> list[dict]:
    """The `tab-action` records of these logs, at or after `since` (an ISO date or time) when given."""
    out = []
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("kind") == "tab-action" and (
                    since is None or str(record.get("at") or "") >= since):
                out.append(record)
    return out


def summarise(records: list[dict]) -> list[dict]:
    """One row per action and way (shown or quiet): how many, how many did not work, and each part's p50 and p95."""
    groups: dict[tuple[str, bool], list[dict]] = {}
    for r in records:
        groups.setdefault((str(r.get("action")), bool(r.get("show", True))), []).append(r)
    rows = []
    for (action, shown), rs in sorted(groups.items()):
        ok = [r for r in rs if r.get("outcome") == "ok"]
        row = {"action": action, "shown": shown, "n": len(rs), "failed": len(rs) - len(ok)}
        for part in PARTS:
            values = [float(r[part]) for r in ok if isinstance(r.get(part), (int, float))]
            row[part] = {"p50": percentile(values, 50), "p95": percentile(values, 95)}
        target = TARGETS_MS.get(action, ANY_MS)
        p50 = row["totalMs"]["p50"]
        row["target"] = target
        row["meets"] = None if p50 is None else p50 <= target
        rows.append(row)
    return rows


def show(rows: list[dict]) -> str:
    def ms(v):
        return "-" if v is None else f"{v:.0f}"

    lines = [f"{'action':<10} {'way':<6} {'n':>4} {'fail':>4}  {'studio p50/p95':>15}  {'screen p50/p95':>15}  "
             f"{'total p50/p95':>15}  target"]
    for r in rows:
        parts = [f"{ms(r[p]['p50'])}/{ms(r[p]['p95'])}" for p in PARTS]
        verdict = "-" if r["meets"] is None else ("meets" if r["meets"] else "misses")
        lines.append(f"{r['action']:<10} {'shown' if r['shown'] else 'quiet':<6} {r['n']:>4} {r['failed']:>4}  "
                     f"{parts[0]:>15}  {parts[1]:>15}  {parts[2]:>15}  ≤{r['target']:.0f} ms {verdict}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", action="append", type=Path,
                        help="an interactions.jsonl to read (repeatable); default: this home's")
    parser.add_argument("--since", help="only records at or after this ISO date or time")
    parser.add_argument("--json", action="store_true", help="print the rows as JSON")
    args = parser.parse_args(argv)
    if args.log:
        logs = args.log
    else:
        from orchestrator.paths import Paths

        logs = [Paths.from_env().interaction_log]
    rows = summarise(read(logs, args.since))
    if not rows:
        print("No Tab actions logged yet in " + ", ".join(str(p) for p in logs), file=sys.stderr)
        return 1
    print(json.dumps(rows, indent=1) if args.json else show(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
