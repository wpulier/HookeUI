"""`python -m orchestrator.maintenance status [KEY] | check KEY | attempt KEY | policy KEY POLICY [GENERATION] | undo KEY`

`attempt` is what the app's Maintainer runs, in its own low-priority process. The others are for developers and tests;
`policy` records a person's choice typed at the command line (how "command-line").
"""
from __future__ import annotations

import argparse
import json
import sys

from gpu_worker import manifests as catalog


def main(argv: list[str] | None = None) -> int:
    from . import discover, policy, prepare, promote, status

    parser = argparse.ArgumentParser(description="Capability maintenance (ADR 0025).")
    parser.add_argument("action", choices=("status", "check", "attempt", "policy", "undo"))
    parser.add_argument("key", nargs="?", default="")
    parser.add_argument("value", nargs="?", default="")
    parser.add_argument("generation", nargs="?", default="")
    parser.add_argument("--again", action="store_true", help="attempt: look at the channel now (a person asked)")
    parser.add_argument("--as-started", action="store_true",
                        help="attempt: keep the priority it was started with (a timed test of the flow)")
    args = parser.parse_args(argv)
    if args.action == "attempt":
        from ..thumbnails import background_priority

        if not args.as_started:  # on a busy Mac this call itself waits minutes: the thread is demoted before it returns
            background_priority()
        print(json.dumps(prepare.run(args.key, again=args.again)))
        return 0
    if args.action == "status" and not args.key:
        rows = {k: status.of(m) for k, m in {**catalog.manifests(), **catalog.mine()}.items() if m.runner == "python-env"}
        print(json.dumps({k: v for k, v in rows.items() if v.get("state") not in (None, "none")
                          or v.get("policy", {}).get("policy") != "manual"}, indent=1))
        return 0
    m = catalog.get(args.key)
    if m is None:
        print(f"there is no entry called {args.key}", file=sys.stderr)
        return 1
    try:
        if args.action == "status":
            out = status.of(m)
        elif args.action == "check":
            out = discover.check(m)
        elif args.action == "policy":
            out = policy.record(m, args.value, by="command line", how="command-line", generation=args.generation)
        else:
            out = promote.undo(m)
    except (policy.NotRecorded, promote.Failed) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
